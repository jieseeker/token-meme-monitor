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


@dataclass(frozen=True)
class PredictionConfidence:
    title: str
    body: str
    tone: str
    chips: tuple[str, ...]
    evidence: tuple[str, ...]


DISPLAY_TIER_LABELS = {
    "launch": "启动异动",
    "risk_momentum": "高风险动量",
    "strong": "强确认",
    "overextended": "已涨过多",
    "normal": "普通观察",
}

OVEREXTENDED_PREDICTION_REASONS = {
    "prediction_overextended_h1",
    "prediction_h1_overextended",
    "prediction_h4_overextended",
    "prediction_overextended_24h",
    "prediction_h24_overextended",
}


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
    short_momentum_score = pd.to_numeric(
        _series_from_frame(overview_df, "prediction_short_momentum_score", overview_df.index),
        errors="coerce",
    )
    overview_df["prediction_short_momentum_score"] = short_momentum_score.where(
        short_momentum_score.notna(),
        overview_df["prediction_opportunity_score"],
    )
    overview_df["prediction_continuation_score"] = pd.to_numeric(
        _series_from_frame(overview_df, "prediction_continuation_score", overview_df.index),
        errors="coerce",
    )
    overview_df["prediction_breakout_score"] = pd.to_numeric(
        _series_from_frame(overview_df, "prediction_breakout_score", overview_df.index),
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
    overview_df["has_prediction_opportunity"] = overview_df["prediction_short_momentum_score"].fillna(-1) >= 45
    overview_df["sort_live_score"] = pd.to_numeric(overview_df["last_score"], errors="coerce").fillna(-1)
    overview_df["sort_alpha_score"] = pd.to_numeric(overview_df["alpha_score"], errors="coerce").fillna(-1)
    overview_df["candidate_strength"] = pd.concat(
        [
            overview_df["display_score"],
            overview_df["prediction_short_momentum_score"].fillna(-1),
        ],
        axis=1,
    ).max(axis=1)
    tier_frame = overview_df.apply(_build_display_tier, axis=1, result_type="expand")
    overview_df["display_tier"] = tier_frame["tier"]
    overview_df["display_tier_label"] = tier_frame["label"]
    overview_df["display_tier_rank"] = pd.to_numeric(tier_frame["rank"], errors="coerce").fillna(0)
    overview_df["display_priority_score"] = pd.to_numeric(tier_frame["priority"], errors="coerce").fillna(
        overview_df["candidate_strength"]
    )
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
    if "prediction_short_momentum_score" in dedupe_df.columns:
        dedupe_df["_prediction_primary_score"] = pd.to_numeric(
            dedupe_df["prediction_short_momentum_score"],
            errors="coerce",
        ).fillna(-1)
    elif "prediction_opportunity_score" in dedupe_df.columns:
        dedupe_df["_prediction_primary_score"] = pd.to_numeric(dedupe_df["prediction_opportunity_score"], errors="coerce").fillna(-1)
    else:
        dedupe_df["_prediction_primary_score"] = -1
    if "signal_score" in dedupe_df.columns:
        dedupe_df["_selection_score"] = pd.to_numeric(dedupe_df["signal_score"], errors="coerce").fillna(-1)
    else:
        dedupe_df["_selection_score"] = -1
    if "display_tier_rank" in dedupe_df.columns:
        dedupe_df["_display_tier_rank"] = pd.to_numeric(dedupe_df["display_tier_rank"], errors="coerce").fillna(0)
    else:
        dedupe_df["_display_tier_rank"] = 0
    if "display_priority_score" in dedupe_df.columns:
        dedupe_df["_display_priority_score"] = pd.to_numeric(dedupe_df["display_priority_score"], errors="coerce").fillna(-1)
    else:
        dedupe_df["_display_priority_score"] = -1
    with_token = dedupe_df[dedupe_df["_token_key"] != ""].copy()
    without_token = dedupe_df[dedupe_df["_token_key"] == ""].copy()
    if not with_token.empty:
        with_token = (
            with_token.sort_values(
                by=[
                    "_token_key",
                    "_live_rank",
                    "_display_tier_rank",
                    "_display_priority_score",
                    "_prediction_rank",
                    "_prediction_primary_score",
                    "_signal_rank",
                    "_selection_score",
                    "pair_pool_liquidity",
                    "snapshot_observed_at_dt",
                    "sort_alpha_score",
                ],
                ascending=[True, False, False, False, False, False, False, False, False, False, False],
            )
            .drop_duplicates(subset=["_token_key"], keep="first")
            .sort_index()
        )
    return pd.concat([with_token, without_token]).sort_index().drop(
        columns=[
            "_token_key",
            "_live_rank",
            "_signal_rank",
            "_prediction_rank",
            "_prediction_primary_score",
            "_selection_score",
            "_display_tier_rank",
            "_display_priority_score",
        ],
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
    derived_df["sort_prediction_primary_score"] = pd.to_numeric(
        derived_df.get("prediction_short_momentum_score"),
        errors="coerce",
    ).fillna(-1)
    derived_df["sort_display_tier_rank"] = pd.to_numeric(derived_df.get("display_tier_rank"), errors="coerce").fillna(0)
    derived_df["sort_display_priority_score"] = pd.to_numeric(
        derived_df.get("display_priority_score"),
        errors="coerce",
    ).fillna(derived_df["candidate_strength"])
    derived_df = derived_df.sort_values(
        by=[
            "sort_has_snapshot",
            "sort_display_tier_rank",
            "sort_display_priority_score",
            "sort_prediction_primary_score",
            "sort_live_score",
            "sort_snapshot",
            "sort_alpha_score",
        ],
        ascending=[False, False, False, False, False, False, False],
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


def build_prediction_confidence(row: Mapping[str, Any]) -> PredictionConfidence:
    prediction_reasons = set(_json_list(_row_get(row, "prediction_reasons")))
    opportunity = _coerce_float(
        first_non_missing(
            _row_get(row, "prediction_short_momentum_score"),
            _row_get(row, "prediction_opportunity_score"),
        )
    )
    opportunity = -1.0 if opportunity is None else opportunity

    if "prediction_empirical_sparse" in prediction_reasons:
        return PredictionConfidence(
            title="历史样本不足",
            body="相似历史样本还不够，当前概率主要来自规则概率；实盘复核时要优先看量能延续、买卖结构和回撤风险。",
            tone="neutral",
            chips=("样本不足", "规则概率"),
            evidence=("校准样本不足", "不主动上调概率", "优先复核量能延续"),
        )

    calibrated_reasons = {
        "prediction_empirical_calibrated",
        "prediction_empirical_lowered",
        "prediction_empirical_raised",
    }
    if prediction_reasons & calibrated_reasons:
        if "prediction_empirical_lowered" in prediction_reasons:
            direction = "历史校准下调"
        elif "prediction_empirical_raised" in prediction_reasons:
            direction = "历史校准上调"
        else:
            direction = "历史命中率校准"
        return PredictionConfidence(
            title="已叠加历史校准",
            body="概率已结合相似历史分段的历史命中率做保守校准；如果当前量价已经过热，仍需按追高风险处理。",
            tone="accent",
            chips=("历史命中率", "保守校准"),
            evidence=(direction, "相似样本已达到校准门槛", "仍需结合最新量价"),
        )

    if opportunity >= 55:
        return PredictionConfidence(
            title="高分段仍需复核",
            body="短线机会进入高分区，但当前没有足量历史样本桶支撑，概率不能当作确定性信号。",
            tone="warn",
            chips=("高分段", "需复核"),
            evidence=("未命中足量历史桶", "重点确认是否已过热", "优先看买卖结构"),
        )

    return PredictionConfidence(
        title="规则概率",
        body="当前概率来自实时指标规则，后续会随着更多 outcome 样本继续校准。",
        tone="neutral",
        chips=("规则模型", "等待校准"),
        evidence=("尚未触发历史校准", "适合观察排序", "不作为确定性信号"),
    )


def _build_display_tier(row: Mapping[str, Any]) -> dict[str, Any]:
    features = _json_dict(_row_get(row, "last_feature_json"))
    reasons = set(_json_list(_row_get(row, "last_reasons")))
    prediction_reasons = set(_json_list(_row_get(row, "prediction_reasons")))
    risk_flags = set(_json_list(_row_get(row, "last_risk_flags"))) | set(_json_list(_row_get(row, "risk_flags")))

    score = _coerce_float(first_non_missing(_row_get(row, "signal_score"), _row_get(row, "last_score"), default=-1))
    score = -1.0 if score is None else score
    opportunity = _coerce_float(first_non_missing(_row_get(row, "prediction_short_momentum_score"), _row_get(row, "prediction_opportunity_score")))
    opportunity = -1.0 if opportunity is None else opportunity
    alpha_score = _coerce_float(_row_get(row, "alpha_score"))
    alpha_score = -1.0 if alpha_score is None else alpha_score
    strength = max(score, opportunity, alpha_score if score < 0 and opportunity < 0 else -1.0)

    volume_h1 = _coerce_float(first_non_missing(features.get("volume_h1"), _row_get(row, "volume_h1"), default=0))
    volume_h1 = 0.0 if volume_h1 is None else volume_h1
    volume_to_liquidity_h1 = _coerce_float(features.get("volume_to_liquidity_h1"))
    volume_to_liquidity_h1 = 0.0 if volume_to_liquidity_h1 is None else volume_to_liquidity_h1
    price_change_m5 = _coerce_float(features.get("price_change_m5"))
    price_change_m5 = 0.0 if price_change_m5 is None else price_change_m5
    price_change_h1 = _coerce_float(features.get("price_change_h1"))
    price_change_h1 = 0.0 if price_change_h1 is None else price_change_h1
    h1_return_live = _coerce_float(features.get("h1_return_live"))
    h4_return_live = _coerce_float(features.get("h4_return_live"))
    h24_return_live = _coerce_float(features.get("h24_return_live"))
    pair_state = str(first_non_missing(_row_get(row, "selection_pair_state"), _row_get(row, "last_pair_state"), _row_get(row, "state"), default=""))

    severe_risk = bool({"missing_price", "liquidity_near_zero"} & risk_flags)
    structural_risk = bool({"fdv_liquidity_stretched", "sell_pressure", "missing_project_metadata"} & risk_flags)
    turnover_hot = volume_to_liquidity_h1 >= 2.0 or "volume_to_liquidity_breakout" in reasons
    volume_hot = volume_h1 >= 50_000
    price_accelerating = (
        price_change_h1 >= 12
        or price_change_m5 >= 8
        or "positive_price_trend" in reasons
        or "prediction_price_accelerating" in prediction_reasons
    )
    attention_hot = (
        alpha_score >= 100
        or "alpha_hot_score" in reasons
        or "binance_futures_listed" in reasons
        or "prediction_alpha_hot" in prediction_reasons
        or "prediction_futures_attention" in prediction_reasons
    )
    launch_momentum = not severe_risk and (
        (volume_hot and turnover_hot and (price_accelerating or attention_hot))
        or (opportunity >= 55 and turnover_hot and volume_hot)
        or (price_change_h1 >= 20 and volume_h1 >= 30_000)
    )
    strong_confirmed = (
        coerce_metadata_bool(_row_get(row, "last_should_alert"))
        or pair_state == "alerted"
        or (score >= 78 and not severe_risk)
    )
    overextended = (
        bool(prediction_reasons & OVEREXTENDED_PREDICTION_REASONS)
        or (h1_return_live is not None and h1_return_live > 0.70)
        or (h4_return_live is not None and h4_return_live > 1.60)
        or (h24_return_live is not None and h24_return_live > 3.00)
        or price_change_h1 >= 70.0
        or price_change_m5 >= 25.0
    )

    if overextended and not severe_risk:
        tier = "overextended"
        rank = 180
    elif launch_momentum and structural_risk:
        tier = "risk_momentum"
        rank = 350
    elif launch_momentum:
        tier = "launch"
        rank = 400
    elif strong_confirmed:
        tier = "strong"
        rank = 300
    else:
        tier = "normal"
        rank = 100

    momentum_bonus = min(volume_to_liquidity_h1, 10.0) * 2.0 + min(max(price_change_h1, 0.0), 100.0) * 0.2
    volume_bonus = min(volume_h1 / 10_000.0, 25.0)
    priority = rank * 1_000.0 + max(strength, 0.0) + momentum_bonus + volume_bonus
    return {
        "tier": tier,
        "label": DISPLAY_TIER_LABELS[tier],
        "rank": rank,
        "priority": priority,
    }


def _json_dict(value: Any) -> dict[str, Any]:
    parsed = json_loads(value, {}) if isinstance(value, str) else value
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[str]:
    parsed = json_loads(value, []) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item not in (None, "")]


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


def _coerce_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
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
