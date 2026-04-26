from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from token_meme_monitor.utils import first_non_missing, json_loads


@dataclass(frozen=True)
class SignalContext:
    observed_at: str | None
    score: int | None
    pair_state: str | None
    should_alert: bool | None
    reasons: tuple[str, ...]
    risk_flags: tuple[str, ...]
    features: dict[str, Any]


def parse_token_metadata(raw_metadata: str | None) -> dict[str, Any]:
    metadata = json_loads(raw_metadata, {})
    return metadata if isinstance(metadata, dict) else {}


def build_database_revision_key(database_path: str) -> tuple[tuple[str, int, int], ...]:
    revisions: list[tuple[str, int, int]] = []
    for label, suffix in (("db", ""), ("wal", "-wal"), ("shm", "-shm")):
        path = Path(f"{database_path}{suffix}")
        try:
            stat_result = path.stat()
        except FileNotFoundError:
            revisions.append((label, 0, 0))
            continue
        revisions.append((label, stat_result.st_mtime_ns, stat_result.st_size))
    return tuple(revisions)


def coerce_metadata_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and pd.isna(value):
        return False
    if value is pd.NA:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def normalize_pair_value(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if value in (None, ""):
        return None
    return str(value)


def resolve_selected_pair(
    pair_ids: list[str],
    *,
    widget_selected_pair: Any,
    session_selected_pair: Any,
    query_selected_pair: Any,
    query_has_priority: bool = True,
) -> str | None:
    if not pair_ids:
        return None
    candidates = (
        (query_selected_pair, widget_selected_pair, session_selected_pair)
        if query_has_priority
        else (widget_selected_pair, session_selected_pair, query_selected_pair)
    )
    for candidate in candidates:
        normalized = normalize_pair_value(candidate)
        if normalized in pair_ids:
            return normalized
    return pair_ids[0]


def build_overview_frame(raw_overview_df: pd.DataFrame) -> pd.DataFrame:
    if raw_overview_df.empty:
        return raw_overview_df.copy()

    overview_df = raw_overview_df.copy()
    now = pd.Timestamp.utcnow()
    token_meta = overview_df["token_metadata_json"].map(parse_token_metadata)
    token_meta_frame = pd.DataFrame(token_meta.tolist(), index=overview_df.index)

    overview_df["token_meta"] = token_meta
    overview_df["is_binance_alpha"] = _series_from_frame(
        token_meta_frame,
        "is_binance_alpha",
        overview_df.index,
        False,
    ).map(coerce_metadata_bool)
    overview_df = overview_df[overview_df["is_binance_alpha"]].copy()
    if overview_df.empty:
        return overview_df

    token_meta_frame = pd.DataFrame(overview_df["token_meta"].tolist(), index=overview_df.index)
    for column, metadata_key in {
        "holder_count": "holder_count",
        "alpha_score": "alpha_score",
        "alpha_market_cap": "alpha_market_cap",
        "alpha_fdv": "alpha_fdv",
        "alpha_price": "alpha_price",
        "alpha_liquidity": "alpha_liquidity",
        "alpha_volume_24h": "alpha_volume_24h",
    }.items():
        overview_df[column] = _series_from_frame(token_meta_frame, metadata_key, overview_df.index)

    overview_df["has_market_data"] = (
        overview_df["price_usd"].notna()
        | overview_df["market_cap"].notna()
        | overview_df["alpha_price"].notna()
        | overview_df["alpha_market_cap"].notna()
    )
    overview_df["snapshot_observed_at_dt"] = pd.to_datetime(overview_df["snapshot_observed_at"], errors="coerce", utc=True)
    overview_df["is_live_active"] = overview_df["snapshot_observed_at_dt"].notna() & (
        overview_df["snapshot_observed_at_dt"] >= now - pd.Timedelta(minutes=15)
    )
    overview_df["has_recent_snapshot"] = overview_df["is_live_active"]
    overview_df["display_score"] = (
        pd.to_numeric(overview_df["last_score"], errors="coerce")
        .combine_first(pd.to_numeric(overview_df["alpha_score"], errors="coerce"))
        .fillna(-1)
    )
    overview_df["display_holders"] = pd.to_numeric(overview_df["holder_count"], errors="coerce").fillna(0)
    pair_liquidity = pd.to_numeric(overview_df["liquidity_usd"], errors="coerce")
    alpha_liquidity = pd.to_numeric(overview_df["alpha_liquidity"], errors="coerce")
    overview_df["display_liquidity"] = pair_liquidity.where(pair_liquidity.notna(), alpha_liquidity).fillna(0)
    overview_df["pair_pool_liquidity"] = pd.to_numeric(overview_df["liquidity_usd"], errors="coerce").fillna(0)
    overview_df["signal_score"] = pd.to_numeric(overview_df["last_score"], errors="coerce")
    overview_df["prediction_opportunity_score"] = pd.to_numeric(
        _series_from_frame(overview_df, "prediction_opportunity_score", overview_df.index),
        errors="coerce",
    )
    for column in (
        "prediction_prob_2h_up20",
        "prediction_prob_6h_up50",
        "prediction_prob_24h_up100",
        "prediction_risk_6h_dd30",
    ):
        overview_df[column] = pd.to_numeric(_series_from_frame(overview_df, column, overview_df.index), errors="coerce")
    latest_pair_state = (
        overview_df["last_pair_state"]
        if "last_pair_state" in overview_df.columns
        else pd.Series(index=overview_df.index, dtype="object")
    )
    current_pair_state = (
        overview_df["state"]
        if "state" in overview_df.columns
        else pd.Series(index=overview_df.index, dtype="object")
    )
    overview_df["selection_pair_state"] = latest_pair_state.combine_first(current_pair_state)
    overview_df["has_pair_signal"] = (
        overview_df["selection_pair_state"].isin(["focused", "alerted"])
        | (overview_df["signal_score"].fillna(-1) >= 65)
    )
    overview_df["has_prediction_opportunity"] = overview_df["prediction_opportunity_score"].fillna(-1) >= 45
    overview_df["sort_live_score"] = pd.to_numeric(overview_df["last_score"], errors="coerce").fillna(-1)
    overview_df["sort_alpha_score"] = pd.to_numeric(overview_df["alpha_score"], errors="coerce").fillna(-1)
    overview_df["candidate_strength"] = pd.concat(
        [
            overview_df["display_score"],
            overview_df["prediction_opportunity_score"].fillna(-1),
        ],
        axis=1,
    ).max(axis=1)
    return keep_representative_pair_per_token(overview_df)


def keep_representative_pair_per_token(overview_df: pd.DataFrame) -> pd.DataFrame:
    if overview_df.empty or "token_address" not in overview_df.columns:
        return overview_df.copy()
    dedupe_df = overview_df.copy()
    dedupe_df["_token_key"] = dedupe_df["token_address"].fillna("").astype(str).str.lower()
    dedupe_df["_live_rank"] = dedupe_df["is_live_active"].astype(int) if "is_live_active" in dedupe_df.columns else 0
    dedupe_df["_signal_rank"] = dedupe_df["has_pair_signal"].astype(int) if "has_pair_signal" in dedupe_df.columns else 0
    dedupe_df["_prediction_rank"] = (
        dedupe_df["has_prediction_opportunity"].astype(int) if "has_prediction_opportunity" in dedupe_df.columns else 0
    )
    if "prediction_opportunity_score" in dedupe_df.columns:
        dedupe_df["_opportunity_score"] = pd.to_numeric(
            dedupe_df["prediction_opportunity_score"],
            errors="coerce",
        ).fillna(-1)
    else:
        dedupe_df["_opportunity_score"] = -1
    if "signal_score" in dedupe_df.columns:
        dedupe_df["_selection_score"] = pd.to_numeric(dedupe_df["signal_score"], errors="coerce").fillna(-1)
    else:
        dedupe_df["_selection_score"] = -1
    with_token = dedupe_df[dedupe_df["_token_key"] != ""].copy()
    without_token = dedupe_df[dedupe_df["_token_key"] == ""].copy()
    if not with_token.empty:
        with_token = (
            with_token.sort_values(
                by=[
                    "_token_key",
                    "_live_rank",
                    "_prediction_rank",
                    "_opportunity_score",
                    "_signal_rank",
                    "_selection_score",
                    "pair_pool_liquidity",
                    "snapshot_observed_at_dt",
                    "sort_alpha_score",
                ],
                ascending=[True, False, False, False, False, False, False, False, False],
            )
            .drop_duplicates(subset=["_token_key"], keep="first")
            .sort_index()
        )
    return pd.concat([with_token, without_token]).sort_index().drop(
        columns=["_token_key", "_live_rank", "_signal_rank", "_prediction_rank", "_opportunity_score", "_selection_score"],
        errors="ignore",
    )


def filter_overview_frame(
    overview_df: pd.DataFrame,
    *,
    min_signal: int,
    min_holders: int,
    min_liquidity: int,
    only_with_market_data: bool,
    now: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if overview_df.empty:
        return overview_df.copy(), overview_df.copy()

    now = now or pd.Timestamp.utcnow()
    active_cutoff = now - pd.Timedelta(minutes=15)
    derived_df = overview_df.copy()
    derived_df["is_live_active"] = derived_df["snapshot_observed_at_dt"].notna() & (derived_df["snapshot_observed_at_dt"] >= active_cutoff)
    derived_df["has_recent_snapshot"] = derived_df["is_live_active"]
    derived_df["sort_has_snapshot"] = derived_df["has_recent_snapshot"].astype(int)
    derived_df["sort_snapshot"] = derived_df["snapshot_observed_at_dt"]
    derived_df["sort_opportunity_score"] = pd.to_numeric(
        derived_df.get("prediction_opportunity_score"),
        errors="coerce",
    ).fillna(-1)
    derived_df = derived_df.sort_values(
        by=["sort_has_snapshot", "sort_opportunity_score", "sort_live_score", "sort_snapshot", "sort_alpha_score"],
        ascending=[False, False, False, False, False],
    )

    filtered_df = derived_df[
        derived_df["is_live_active"]
        & (derived_df["candidate_strength"] >= min_signal)
        & (derived_df["display_holders"] >= min_holders)
        & (derived_df["display_liquidity"] >= min_liquidity)
    ]
    if only_with_market_data:
        filtered_df = filtered_df[filtered_df["has_market_data"]]
    return derived_df, filtered_df


def build_signal_context(signal_row: Mapping[str, Any] | None) -> SignalContext | None:
    if signal_row is None:
        return None
    return _build_signal_context_from_fields(
        observed_at=_row_get(signal_row, "observed_at"),
        score=_row_get(signal_row, "score"),
        pair_state=_row_get(signal_row, "pair_state"),
        should_alert=_row_get(signal_row, "should_alert"),
        reasons=_row_get(signal_row, "reasons"),
        risk_flags=_row_get(signal_row, "risk_flags"),
        feature_json=_row_get(signal_row, "feature_json"),
    )


def build_latest_signal_context(overview_row: Mapping[str, Any]) -> SignalContext | None:
    return _build_signal_context_from_fields(
        observed_at=_row_get(overview_row, "last_signal_at"),
        score=_row_get(overview_row, "last_score"),
        pair_state=_row_get(overview_row, "last_pair_state"),
        should_alert=_row_get(overview_row, "last_should_alert"),
        reasons=_row_get(overview_row, "last_reasons"),
        risk_flags=_row_get(overview_row, "last_risk_flags"),
        feature_json=_row_get(overview_row, "last_feature_json"),
    )


def metric_value(signal_context: SignalContext | None, overview_row: Mapping[str, Any], name: str) -> Any:
    if signal_context is not None and name in signal_context.features:
        return signal_context.features.get(name)
    return _row_get(overview_row, name)


def resolve_score(signal_context: SignalContext | None, overview_row: Mapping[str, Any]) -> int | None:
    if signal_context is not None and signal_context.score is not None:
        return signal_context.score
    return _coerce_int(_row_get(overview_row, "alpha_score"))


def resolve_pair_state(signal_context: SignalContext | None, overview_row: Mapping[str, Any]) -> str | None:
    if signal_context is not None and signal_context.pair_state:
        return signal_context.pair_state
    value = _row_get(overview_row, "state")
    return None if _is_missing(value) else str(value)


def build_conclusion(
    signal_context: SignalContext | None,
    overview_row: Mapping[str, Any],
    *,
    explain_reason: Callable[[str], tuple[str, str]],
    explain_risk: Callable[[str], tuple[str, str]],
) -> dict[str, Any]:
    score = resolve_score(signal_context, overview_row)
    holders = _coerce_int(_row_get(overview_row, "holder_count")) or 0
    liquidity_raw = first_non_missing(
        metric_value(signal_context, overview_row, "liquidity_usd"),
        _row_get(overview_row, "alpha_liquidity"),
        default=0,
    )
    try:
        liquidity = float(liquidity_raw)
    except (TypeError, ValueError):
        liquidity = 0.0
    reasons = list(signal_context.reasons) if signal_context is not None else []
    risk_flags = list(signal_context.risk_flags) if signal_context is not None else []
    has_market_data = any(
        _has_visible_market_value(value)
        for value in [
            metric_value(signal_context, overview_row, "price_usd"),
            metric_value(signal_context, overview_row, "market_cap"),
            _row_get(overview_row, "alpha_price"),
            _row_get(overview_row, "alpha_market_cap"),
        ]
    )

    strengths = [explain_reason(code)[0] for code in reasons[:3]]
    risks = [explain_risk(code)[0] for code in risk_flags[:3]]

    if not has_market_data:
        return {
            "title": "等待行情同步",
            "klass": "verdict-neutral",
            "summary": "该 Alpha 代币已经进入监控池，但当前主行情快照还不完整，市值、价格和成交额会在后续轮询中继续补齐。",
            "action": "继续观察，等待快照同步完成后再看分数和量价结构。",
            "strengths": strengths,
            "risks": risks or ["快照尚未就绪"],
        }

    if "liquidity_near_zero" in risk_flags or liquidity < 5_000:
        return {
            "title": "暂不关注",
            "klass": "verdict-neutral",
            "summary": "虽然代币在 Alpha 池中，但当前流动性过低，价格和成交容易失真，不适合优先跟踪。",
            "action": "先放在观察名单外，除非后续流动性和成交额明显恢复。",
            "strengths": strengths,
            "risks": risks or ["流动性过低"],
        }

    if score is not None and score >= 78 and holders >= 1_000 and liquidity >= 15_000 and "sell_pressure" not in risk_flags:
        return {
            "title": "重点关注",
            "klass": "verdict-good",
            "summary": "这是一条强信号标的。分数、持币人数和流动性都达到较高水平，且当前没有明显的卖压型风险。",
            "action": "优先放入重点观察池，持续盯住量能是否延续，以及后续 5 分钟买卖结构是否继续偏强。",
            "strengths": strengths or ["分数高", "持币人数较健康", "流动性达标"],
            "risks": risks or ["暂未发现显著风险"],
        }

    if score is not None and score >= 65:
        return {
            "title": "继续跟踪",
            "klass": "verdict-warn",
            "summary": "当前已经具备一定强度，但还没有强到可以直接列为最优先目标，通常还差进一步确认。",
            "action": "继续观察接下来几轮量价表现，重点看 1 小时成交额和买盘优势能否延续。",
            "strengths": strengths or ["已进入中高分区间"],
            "risks": risks or ["仍需等待更多确认"],
        }

    return {
        "title": "普通观察",
        "klass": "verdict-neutral",
        "summary": "当前仍处在观察名单阶段，已有一定基础数据，但强度暂时不够高，不建议优先处理。",
        "action": "保留观察即可，等分数抬升、流动性改善或成交放量后再提升优先级。",
        "strengths": strengths or ["已进入 Alpha 观察宇宙"],
        "risks": risks or ["当前强度一般"],
    }


def _series_from_frame(frame: pd.DataFrame, column: str, index: pd.Index, default: Any = None) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series([default] * len(index), index=index)


def _build_signal_context_from_fields(
    *,
    observed_at: Any,
    score: Any,
    pair_state: Any,
    should_alert: Any,
    reasons: Any,
    risk_flags: Any,
    feature_json: Any,
) -> SignalContext | None:
    features = json_loads(feature_json, {}) if isinstance(feature_json, str) else {}
    if not isinstance(features, dict):
        features = {}

    reason_values = tuple(str(item) for item in json_loads(reasons, []) if item not in (None, ""))
    risk_values = tuple(str(item) for item in json_loads(risk_flags, []) if item not in (None, ""))
    observed_at_value = None if _is_missing(observed_at) else str(observed_at)
    pair_state_value = None if _is_missing(pair_state) else str(pair_state)
    score_value = _coerce_int(score)
    should_alert_value = _coerce_bool(should_alert)

    if (
        observed_at_value is None
        and score_value is None
        and pair_state_value is None
        and not features
        and not reason_values
        and not risk_values
    ):
        return None

    return SignalContext(
        observed_at=observed_at_value,
        score=score_value,
        pair_state=pair_state_value,
        should_alert=should_alert_value,
        reasons=reason_values,
        risk_flags=risk_values,
        features=features,
    )


def _row_get(row: Mapping[str, Any], name: str) -> Any:
    return row.get(name)


def _coerce_int(value: Any) -> int | None:
    if _is_missing(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool | None:
    if _is_missing(value):
        return None
    return bool(value)


def _has_visible_market_value(value: Any) -> bool:
    if _is_missing(value):
        return False
    try:
        return float(value) != 0
    except (TypeError, ValueError):
        return True


def _is_missing(value: Any) -> bool:
    if value in (None, ""):
        return True
    try:
        return bool(pd.isna(value))
    except TypeError:
        return False
