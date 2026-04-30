from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.prediction_backtest import build_prediction_backtest_report
from token_meme_monitor.prediction_outcomes import compute_prediction_outcome_with_hourly_ohlcv
from token_meme_monitor.strategy_feedback import build_strategy_feedback_report, compact_strategy_feedback_summary
from token_meme_monitor.utils import isoformat_utc, json_dumps, json_loads, parse_datetime, safe_float, safe_int, utcnow


SCHEDULED_BACKTEST_STATE_CACHE_KEY = "runtime:scheduled_backtest:last_run"

OVEREXTENDED_REASONS = {
    "prediction_overextended_h1",
    "prediction_h1_overextended",
    "prediction_h4_overextended",
    "prediction_overextended_24h",
    "prediction_h24_overextended",
}


def run_scheduled_backtest_cycle(
    *,
    database_path: str,
    chain_id: str,
    json_out: str,
    md_out: str,
    archive_dir: str = "data/backtests/scheduled",
    archive: bool = True,
    limit: int | None = None,
    train_ratio: float = 0.70,
    max_price_divergence_pct: float | None = 0.10,
    top_gainers_limit: int = 20,
    strong_gainer_return_threshold: float = 0.20,
    refresh_outcome_limit: int = 1000,
    refresh_missing_quality: bool = False,
    skip_refresh_outcomes: bool = False,
) -> dict[str, Any]:
    try:
        started_at = utcnow()
        now = started_at
        refreshed = 0
        skipped = 0
        repo = MonitorRepository(database_path)
        repo.initialize()
        try:
            if not skip_refresh_outcomes:
                outcome_rows = repo.list_predictions_needing_outcomes(
                    now,
                    limit=refresh_outcome_limit,
                    include_missing_quality=refresh_missing_quality,
                )
                for row in outcome_rows:
                    observed_at = parse_datetime(row.get("observed_at"))
                    if observed_at is None:
                        continue
                    outcome = compute_prediction_outcome_with_hourly_ohlcv(
                        repo,
                        pair_address=row["pair_address"],
                        observed_at=observed_at,
                        feature_json=row.get("feature_json"),
                        network=chain_id,
                        now=now,
                    )
                    if outcome is None:
                        skipped += 1
                        continue
                    repo.upsert_prediction_outcome(int(row["signal_id"]), outcome, evaluated_at=now)
                    refreshed += 1
            rows = repo.list_prediction_dataset_rows(limit=limit)
        finally:
            repo.close()

        report = build_scheduled_backtest_report(
            rows,
            train_ratio=train_ratio,
            max_price_divergence_pct=max_price_divergence_pct,
            top_gainers_limit=top_gainers_limit,
            strong_gainer_return_threshold=strong_gainer_return_threshold,
            generated_at=now,
        )
        write_scheduled_backtest_outputs(report, json_path=json_out, markdown_path=md_out)
        archive_json = None
        archive_md = None
        if archive:
            archive_stamp = now.strftime("%Y%m%d-%H%M")
            archive_path = Path(archive_dir)
            archive_json = archive_path / f"{archive_stamp}.json"
            archive_md = archive_path / f"{archive_stamp}.md"
            write_scheduled_backtest_outputs(report, json_path=str(archive_json), markdown_path=str(archive_md))

        result = {
            "report": report,
            "refreshed": refreshed,
            "skipped": skipped,
            "json_out": json_out,
            "md_out": md_out,
            "archive_json": str(archive_json) if archive_json else "",
            "archive_md": str(archive_md) if archive_md else "",
            "ran_at": now.isoformat(timespec="seconds"),
        }
        finished_at = utcnow()
        record_scheduled_backtest_state(
            database_path,
            status="success",
            started_at=started_at,
            finished_at=finished_at,
            summary={
                **dict(report.get("summary") or {}),
                "refreshed": refreshed,
                "skipped": skipped,
                "json_out": json_out,
                "md_out": md_out,
                "archive_json": result["archive_json"],
                "archive_md": result["archive_md"],
            },
        )
        return result
    except Exception as exc:
        finished_at = utcnow()
        try:
            record_scheduled_backtest_state(
                database_path,
                status="failure",
                started_at=started_at if "started_at" in locals() else finished_at,
                finished_at=finished_at,
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            pass
        raise


def record_scheduled_backtest_state(
    database_path: str,
    *,
    status: str,
    started_at: Any,
    finished_at: Any,
    summary: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> None:
    started = parse_datetime(str(started_at)) if isinstance(started_at, str) else started_at
    finished = parse_datetime(str(finished_at)) if isinstance(finished_at, str) else finished_at
    duration_seconds = None
    if started is not None and finished is not None:
        duration_seconds = round((finished - started).total_seconds(), 3)
    value: dict[str, Any] = {
        "name": "scheduled_backtest",
        "status": status,
        "started_at": isoformat_utc(started),
        "finished_at": isoformat_utc(finished),
        "duration_seconds": duration_seconds,
        "summary": dict(summary or {}),
    }
    if error:
        value["error"] = error
    repo = MonitorRepository(database_path)
    repo.initialize()
    try:
        repo.upsert_external_json_cache(SCHEDULED_BACKTEST_STATE_CACHE_KEY, value, fetched_at=finished)
    finally:
        repo.close()


def build_scheduled_backtest_report(
    rows: list[Mapping[str, Any]],
    *,
    train_ratio: float = 0.70,
    max_price_divergence_pct: float | None = 0.10,
    top_gainers_limit: int = 20,
    strong_gainer_return_threshold: float = 0.20,
    generated_at: Any = None,
) -> dict[str, Any]:
    generated = generated_at or utcnow()
    generated_text = generated.isoformat(timespec="seconds") if hasattr(generated, "isoformat") else str(generated)
    backtest = build_prediction_backtest_report(
        rows,
        train_ratio=train_ratio,
        max_price_divergence_pct=max_price_divergence_pct,
    )
    top_gainers = _top_gainers(rows, limit=top_gainers_limit, max_price_divergence_pct=max_price_divergence_pct)
    missed_strong_gainers = [
        item
        for item in top_gainers
        if item["best_return"] >= strong_gainer_return_threshold and item["miss_reasons"]
    ]
    chase_signals = _chase_signals(rows, limit=top_gainers_limit, max_price_divergence_pct=max_price_divergence_pct)
    warnings = _calibration_warnings(backtest)
    strategy_feedback = compact_strategy_feedback_summary(
        build_strategy_feedback_report(
            rows,
            min_slice_events=30,
            max_price_divergence_pct=max_price_divergence_pct,
            generated_at=generated,
        )
    )
    return {
        "generated_at": generated_text,
        "mode": "scheduled_backtest_report",
        "settings": {
            "train_ratio": train_ratio,
            "max_price_divergence_pct": max_price_divergence_pct,
            "top_gainers_limit": top_gainers_limit,
            "strong_gainer_return_threshold": strong_gainer_return_threshold,
        },
        "summary": {
            "total_rows": len(rows),
            "usable_events": backtest.get("usable_events", 0),
            "test_events": backtest.get("test_events", 0),
            "top_gainer_count": len(top_gainers),
            "missed_strong_gainer_count": len(missed_strong_gainers),
            "chase_signal_count": len(chase_signals),
            "warning_count": len(warnings),
            "strategy_recommendation_count": strategy_feedback.get("recommendation_count", 0),
        },
        "warnings": warnings,
        "backtest": backtest,
        "strategy_feedback": strategy_feedback,
        "top_gainers": top_gainers,
        "missed_strong_gainers": missed_strong_gainers,
        "chase_signals": chase_signals,
    }


def render_scheduled_backtest_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    backtest = report.get("backtest") or {}
    aggregate = backtest.get("aggregate") or {}
    lines = [
        "# Scheduled Backtest Report",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Rows: `{summary.get('total_rows', 0)}`",
        f"- Usable events: `{summary.get('usable_events', 0)}`",
        f"- Test events: `{summary.get('test_events', 0)}`",
        "",
        "## 核心发现",
        "",
        f"- 涨幅榜样本: `{summary.get('top_gainer_count', 0)}`",
        f"- 疑似漏抓: `{summary.get('missed_strong_gainer_count', 0)}`",
        f"- 疑似追高: `{summary.get('chase_signal_count', 0)}`",
        f"- 2h 预测/实际: `{_format_pct(aggregate.get('avg_prob_2h_up20'))}` / `{_format_pct(aggregate.get('actual_2h_up20_rate'))}`",
        f"- 策略反馈建议数: `{(report.get('strategy_feedback') or {}).get('recommendation_count', 0)}`",
    ]
    warnings = report.get("warnings") or []
    if warnings:
        lines.append("- 校准提醒: " + "；".join(str(item) for item in warnings))
    lines.extend(
        [
            "",
            "## 涨幅榜",
            "",
            "| Token | Best | 2h | 24h | Signal | Short | Reasons |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for item in report.get("top_gainers") or []:
        lines.append(
            f"| {item.get('token_symbol') or item.get('token_address')} "
            f"| {_format_pct(item.get('best_return'))} "
            f"| {_format_pct(item.get('max_return_2h'))} "
            f"| {_format_pct(item.get('max_return_24h'))} "
            f"| {item.get('score')} "
            f"| {item.get('short_momentum_score')} "
            f"| {'; '.join(item.get('miss_reasons') or item.get('chase_reasons') or [])} |"
        )
    lines.extend(
        [
            "",
            "## 漏抓分析",
            "",
            "| Token | Best | Signal | Short | Miss reason | Pair |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for item in report.get("missed_strong_gainers") or []:
        lines.append(
            f"| {item.get('token_symbol') or item.get('token_address')} "
            f"| {_format_pct(item.get('best_return'))} "
            f"| {item.get('score')} "
            f"| {item.get('short_momentum_score')} "
            f"| {'; '.join(item.get('miss_reasons') or [])} "
            f"| `{item.get('pair_address')}` |"
        )
    lines.extend(
        [
            "",
            "## 追高风险",
            "",
            "| Token | Best | Signal | Short | Chase reason | Pair |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for item in report.get("chase_signals") or []:
        lines.append(
            f"| {item.get('token_symbol') or item.get('token_address')} "
            f"| {_format_pct(item.get('best_return'))} "
            f"| {item.get('score')} "
            f"| {item.get('short_momentum_score')} "
            f"| {'; '.join(item.get('chase_reasons') or [])} "
            f"| `{item.get('pair_address')}` |"
        )
    return "\n".join(lines) + "\n"


def write_scheduled_backtest_outputs(report: Mapping[str, Any], *, json_path: str, markdown_path: str) -> None:
    json_output = Path(json_path)
    markdown_output = Path(markdown_path)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json_dumps(report) + "\n", encoding="utf-8")
    markdown_output.write_text(render_scheduled_backtest_markdown(report), encoding="utf-8")


def _top_gainers(
    rows: list[Mapping[str, Any]],
    *,
    limit: int,
    max_price_divergence_pct: float | None,
) -> list[dict[str, Any]]:
    candidates = [_row_summary(row) for row in rows if _usable_quality(row, max_price_divergence_pct)]
    candidates = [item for item in candidates if item["best_return"] is not None]
    candidates = _dedupe_summaries(candidates, key_func=lambda item: safe_float(item.get("best_return")) or -1.0)
    return sorted(candidates, key=lambda item: item["best_return"], reverse=True)[:limit]


def _chase_signals(
    rows: list[Mapping[str, Any]],
    *,
    limit: int,
    max_price_divergence_pct: float | None,
) -> list[dict[str, Any]]:
    candidates = []
    for row in rows:
        if not _usable_quality(row, max_price_divergence_pct):
            continue
        summary = _row_summary(row)
        if summary["chase_reasons"]:
            candidates.append(summary)
    candidates = _dedupe_summaries(
        candidates,
        key_func=lambda item: (safe_int(item.get("short_momentum_score")), safe_int(item.get("score"))),
    )
    return sorted(candidates, key=lambda item: (item["short_momentum_score"], item["score"]), reverse=True)[:limit]


def _dedupe_summaries(items: list[dict[str, Any]], *, key_func: Callable[[Mapping[str, Any]], Any]) -> list[dict[str, Any]]:
    best_by_identity: dict[str, dict[str, Any]] = {}
    best_keys: dict[str, Any] = {}
    for item in items:
        identity = _summary_identity(item)
        item_key = key_func(item)
        if identity not in best_by_identity or item_key > best_keys[identity]:
            best_by_identity[identity] = item
            best_keys[identity] = item_key
    return list(best_by_identity.values())


def _summary_identity(item: Mapping[str, Any]) -> str:
    token_address = str(item.get("token_address") or "").strip().lower()
    if token_address:
        return f"token:{token_address}"
    pair_address = str(item.get("pair_address") or "").strip().lower()
    if pair_address:
        return f"pair:{pair_address}"
    return f"signal:{item.get('signal_id')}"


def _row_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    returns = [
        safe_float(row.get("max_return_2h")),
        safe_float(row.get("max_return_6h")),
        safe_float(row.get("max_return_24h")),
    ]
    best_return = max([value for value in returns if value is not None], default=None)
    score = safe_int(row.get("score"))
    short_score = safe_int(row.get("short_momentum_score") if row.get("short_momentum_score") is not None else row.get("opportunity_score"))
    prediction_reasons = _json_list(row.get("prediction_reasons"))
    features = _json_dict(row.get("feature_json"))
    stage = str(row.get("stage") or "")
    miss_reasons = _miss_reasons(score=score, short_score=short_score, row=row)
    chase_reasons = _chase_reasons_for_row(
        score=score,
        short_score=short_score,
        stage=stage,
        prediction_reasons=prediction_reasons,
        features=features,
    )
    observed_at = parse_datetime(str(row.get("observed_at"))) if row.get("observed_at") else None
    return {
        "signal_id": row.get("signal_id"),
        "pair_address": row.get("pair_address"),
        "token_address": row.get("token_address"),
        "token_symbol": row.get("token_symbol"),
        "token_name": row.get("token_name"),
        "observed_at": observed_at.isoformat(timespec="seconds") if observed_at else row.get("observed_at"),
        "score": score,
        "short_momentum_score": short_score,
        "stage": stage,
        "best_return": best_return,
        "max_return_2h": safe_float(row.get("max_return_2h")),
        "max_return_6h": safe_float(row.get("max_return_6h")),
        "max_return_24h": safe_float(row.get("max_return_24h")),
        "prediction_reasons": prediction_reasons,
        "miss_reasons": miss_reasons,
        "chase_reasons": chase_reasons,
    }


def _miss_reasons(*, score: int, short_score: int, row: Mapping[str, Any]) -> list[str]:
    reasons = []
    if short_score < 45:
        reasons.append("短线机会分低于45")
    if score < 65:
        reasons.append("信号分低于65")
    if str(row.get("pair_state") or "") not in {"focused", "alerted"}:
        reasons.append("未进入重点状态")
    return reasons


def _chase_reasons_for_row(
    *,
    score: int,
    short_score: int,
    stage: str,
    prediction_reasons: list[str],
    features: Mapping[str, Any],
) -> list[str]:
    reasons = []
    h1_return = safe_float(features.get("h1_return_live"))
    h4_return = safe_float(features.get("h4_return_live"))
    h24_return = safe_float(features.get("h24_return_live"))
    is_overextended = (
        stage == "exhaustion"
        or bool(set(prediction_reasons) & OVEREXTENDED_REASONS)
        or (h1_return is not None and h1_return > 0.70)
        or (h4_return is not None and h4_return > 1.60)
        or (h24_return is not None and h24_return > 3.00)
    )
    if is_overextended and (score >= 65 or short_score >= 55):
        reasons.append("信号出现时已过热")
    return reasons


def _calibration_warnings(backtest: Mapping[str, Any]) -> list[str]:
    warnings = []
    aggregate = backtest.get("aggregate") or {}
    predicted_2h = safe_float(aggregate.get("avg_prob_2h_up20"))
    actual_2h = safe_float(aggregate.get("actual_2h_up20_rate"))
    if predicted_2h is not None and actual_2h is not None:
        if predicted_2h > actual_2h * 1.75 and predicted_2h - actual_2h >= 0.01:
            warnings.append("2h 概率偏高，建议继续降低高分桶置信度")
        elif actual_2h > predicted_2h * 1.75 and actual_2h - predicted_2h >= 0.01:
            warnings.append("2h 概率偏低，建议检查漏抓特征")
    for bucket, summary in (backtest.get("buckets") or {}).items():
        events = safe_int(summary.get("events"))
        if bucket in {"55-69", "70+"} and events < 30:
            warnings.append(f"{bucket} 高分桶样本不足，仅 {events} 个事件")
    return warnings


def _usable_quality(row: Mapping[str, Any], max_price_divergence_pct: float | None) -> bool:
    divergence = safe_float(row.get("price_divergence_pct"))
    if max_price_divergence_pct is not None and divergence is not None and abs(divergence) > max_price_divergence_pct:
        return False
    return any(
        safe_int(row.get(field)) > 0
        for field in ("sample_count_2h", "sample_count_6h", "sample_count_24h")
    )


def _json_dict(value: Any) -> dict[str, Any]:
    parsed = json_loads(value, {}) if isinstance(value, str) else value
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[str]:
    parsed = json_loads(value, []) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item not in (None, "")]


def _format_pct(value: Any) -> str:
    parsed = safe_float(value)
    if parsed is None:
        return "--"
    return f"{parsed * 100:.2f}%"
