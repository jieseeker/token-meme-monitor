from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from token_meme_monitor.models import SignalDecision
from token_meme_monitor.prediction_outcomes import MIN_OUTCOME_SAMPLE_2H, MIN_OUTCOME_SAMPLE_6H, MIN_OUTCOME_SAMPLE_24H
from token_meme_monitor.predictions import build_prediction_calibration, build_prediction_result
from token_meme_monitor.utils import json_dumps, json_loads, parse_datetime, safe_float, safe_int, utcnow


BUCKET_ORDER = ("<45", "45-54", "55-69", "70+")
DEFAULT_EPISODE_HOURS = 2


def build_prediction_backtest_report(
    rows: list[Mapping[str, Any]],
    *,
    train_ratio: float = 0.70,
    max_price_divergence_pct: float | None = None,
    episode_hours: int = DEFAULT_EPISODE_HOURS,
) -> dict[str, Any]:
    if not 0 < train_ratio < 1:
        raise ValueError("train_ratio must be between 0 and 1")

    usable_rows, quality = _filter_usable_rows(rows, max_price_divergence_pct=max_price_divergence_pct)
    events, duplicate_events_skipped = _dedupe_prediction_events(usable_rows, episode_hours=episode_hours)
    train_events, test_events = _split_events(events, train_ratio=train_ratio)
    records: list[dict[str, Any]] = []
    calibration_rows = list(train_events)
    for row in test_events:
        calibration = build_prediction_calibration(calibration_rows)
        prediction = build_prediction_result(
            _decision_from_row(row),
            token_metadata=_mapping_from_json(row.get("token_metadata_json")),
            calibration=calibration,
        )
        records.append(
            {
                "row": row,
                "prediction": prediction,
                "bucket": _opportunity_bucket(prediction.short_momentum_score),
            }
        )
        calibration_rows.append(row)

    return {
        "generated_at": utcnow().isoformat(timespec="seconds"),
        "mode": "expanding_walk_forward",
        "train_ratio": train_ratio,
        "episode_hours": episode_hours,
        "max_price_divergence_pct": max_price_divergence_pct,
        "total_rows": len(rows),
        "usable_events": len(events),
        "duplicate_events_skipped": duplicate_events_skipped,
        "train_events": len(train_events),
        "test_events": len(test_events),
        "quality": quality,
        "aggregate": _summarize_records(records),
        "buckets": {
            bucket: _summarize_records([record for record in records if record["bucket"] == bucket])
            for bucket in BUCKET_ORDER
        },
    }


def write_prediction_backtest_outputs(report: Mapping[str, Any], *, json_path: str, markdown_path: str) -> None:
    json_output = Path(json_path)
    markdown_output = Path(markdown_path)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json_dumps(report) + "\n", encoding="utf-8")
    markdown_output.write_text(render_prediction_backtest_markdown(report), encoding="utf-8")


def render_prediction_backtest_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Prediction Backtest Report",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Mode: `{report.get('mode')}`",
        f"- Train events: `{report.get('train_events')}`",
        f"- Test events: `{report.get('test_events')}`",
        f"- Usable events: `{report.get('usable_events')}`",
        f"- Duplicate events skipped: `{report.get('duplicate_events_skipped')}`",
        "",
        "| Bucket | Events | Tokens | 2h score | 6h score | 24h score | Pred 2h | Hit 2h | Pred 6h | Hit 6h | Pred 24h | Hit 24h | Risk 6h | DD 6h |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for bucket in BUCKET_ORDER:
        summary = (report.get("buckets") or {}).get(bucket) or {}
        lines.append(_markdown_summary_row(bucket, summary))
    lines.extend(
        [
            "",
            "## Quality",
            "",
            f"- Rows excluded by price divergence: `{(report.get('quality') or {}).get('rows_excluded_by_price_divergence', 0)}`",
            f"- Rows missing usable outcome: `{(report.get('quality') or {}).get('rows_missing_usable_outcome', 0)}`",
            "",
        ]
    )
    return "\n".join(lines)


def _filter_usable_rows(
    rows: list[Mapping[str, Any]],
    *,
    max_price_divergence_pct: float | None,
) -> tuple[list[Mapping[str, Any]], dict[str, Any]]:
    usable_rows: list[Mapping[str, Any]] = []
    rows_excluded_by_price_divergence = 0
    rows_missing_usable_outcome = 0
    rows_with_price_divergence = 0
    quality_flag_counts: Counter[str] = Counter()
    for row in rows:
        quality_flag_counts.update(_quality_flags(row))
        divergence = safe_float(row.get("price_divergence_pct"))
        if divergence is not None:
            rows_with_price_divergence += 1
        if max_price_divergence_pct is not None and divergence is not None and abs(divergence) > max_price_divergence_pct:
            rows_excluded_by_price_divergence += 1
            continue
        if not _has_usable_outcome(row):
            rows_missing_usable_outcome += 1
            continue
        usable_rows.append(row)
    return usable_rows, {
        "rows_with_price_divergence": rows_with_price_divergence,
        "rows_excluded_by_price_divergence": rows_excluded_by_price_divergence,
        "rows_missing_usable_outcome": rows_missing_usable_outcome,
        "quality_flag_counts": dict(sorted(quality_flag_counts.items())),
    }


def _dedupe_prediction_events(
    rows: list[Mapping[str, Any]],
    *,
    episode_hours: int,
) -> tuple[list[Mapping[str, Any]], int]:
    sorted_rows = sorted(rows, key=lambda row: _row_observed_at(row) or datetime.max.replace(tzinfo=timezone.utc))
    events: list[Mapping[str, Any]] = []
    last_event_at: dict[str, datetime] = {}
    duplicate_events_skipped = 0
    for row in sorted_rows:
        observed_at = _row_observed_at(row)
        identity = _event_identity(row)
        if observed_at is None or not identity:
            continue
        previous_at = last_event_at.get(identity)
        if previous_at is not None and observed_at - previous_at < timedelta(hours=episode_hours):
            duplicate_events_skipped += 1
            continue
        last_event_at[identity] = observed_at
        events.append(row)
    return events, duplicate_events_skipped


def _split_events(events: list[Mapping[str, Any]], *, train_ratio: float) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    if len(events) <= 1:
        return list(events), []
    cutoff = int(len(events) * train_ratio)
    cutoff = max(1, min(len(events) - 1, cutoff))
    return list(events[:cutoff]), list(events[cutoff:])


def _summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    token_addresses = {str(record["row"].get("token_address") or "").lower() for record in records if record["row"].get("token_address")}
    pair_addresses = {str(record["row"].get("pair_address") or "").lower() for record in records if record["row"].get("pair_address")}
    quality_flag_counts: Counter[str] = Counter()
    divergences: list[float] = []
    summary = {
        "events": len(records),
        "distinct_tokens": len(token_addresses),
        "distinct_pairs": len(pair_addresses),
        "avg_opportunity_score": _avg([record["prediction"].opportunity_score for record in records]),
        "avg_short_momentum_score": _avg([record["prediction"].short_momentum_score for record in records]),
        "avg_continuation_score": _avg([record["prediction"].continuation_score for record in records]),
        "avg_breakout_score": _avg([record["prediction"].breakout_score for record in records]),
        "avg_prob_2h_up20": _avg([record["prediction"].prob_2h_up20 for record in records]),
        "avg_prob_6h_up50": _avg([record["prediction"].prob_6h_up50 for record in records]),
        "avg_prob_24h_up100": _avg([record["prediction"].prob_24h_up100 for record in records]),
        "avg_risk_6h_dd30": _avg([record["prediction"].risk_6h_dd30 for record in records]),
        "sample_2h_up20": 0,
        "hit_2h_up20": 0,
        "actual_2h_up20_rate": None,
        "sample_6h_up50": 0,
        "hit_6h_up50": 0,
        "actual_6h_up50_rate": None,
        "sample_24h_up100": 0,
        "hit_24h_up100": 0,
        "actual_24h_up100_rate": None,
        "sample_6h_dd30": 0,
        "hit_6h_dd30": 0,
        "actual_6h_dd30_rate": None,
        "avg_abs_price_divergence_pct": None,
        "quality_flag_counts": {},
    }
    for record in records:
        row = record["row"]
        quality_flag_counts.update(_quality_flags(row))
        divergence = safe_float(row.get("price_divergence_pct"))
        if divergence is not None:
            divergences.append(abs(divergence))
        if safe_int(row.get("sample_count_2h")) >= MIN_OUTCOME_SAMPLE_2H:
            summary["sample_2h_up20"] += 1
            summary["hit_2h_up20"] += safe_int(row.get("hit_2h_up20"))
        if safe_int(row.get("sample_count_6h")) >= MIN_OUTCOME_SAMPLE_6H:
            summary["sample_6h_up50"] += 1
            summary["hit_6h_up50"] += safe_int(row.get("hit_6h_up50"))
            summary["sample_6h_dd30"] += 1
            summary["hit_6h_dd30"] += safe_int(row.get("hit_6h_dd30"))
        if safe_int(row.get("sample_count_24h")) >= MIN_OUTCOME_SAMPLE_24H:
            summary["sample_24h_up100"] += 1
            summary["hit_24h_up100"] += safe_int(row.get("hit_24h_up100"))
    summary["actual_2h_up20_rate"] = _rate(summary["hit_2h_up20"], summary["sample_2h_up20"])
    summary["actual_6h_up50_rate"] = _rate(summary["hit_6h_up50"], summary["sample_6h_up50"])
    summary["actual_24h_up100_rate"] = _rate(summary["hit_24h_up100"], summary["sample_24h_up100"])
    summary["actual_6h_dd30_rate"] = _rate(summary["hit_6h_dd30"], summary["sample_6h_dd30"])
    summary["avg_abs_price_divergence_pct"] = _avg(divergences)
    summary["quality_flag_counts"] = dict(sorted(quality_flag_counts.items()))
    return summary


def _markdown_summary_row(bucket: str, summary: Mapping[str, Any]) -> str:
    return (
        f"| {bucket} "
        f"| {safe_int(summary.get('events'))} "
        f"| {safe_int(summary.get('distinct_tokens'))} "
        f"| {_format_number(summary.get('avg_short_momentum_score'))} "
        f"| {_format_number(summary.get('avg_continuation_score'))} "
        f"| {_format_number(summary.get('avg_breakout_score'))} "
        f"| {_format_pct(summary.get('avg_prob_2h_up20'))} "
        f"| {_format_pct(summary.get('actual_2h_up20_rate'))} "
        f"| {_format_pct(summary.get('avg_prob_6h_up50'))} "
        f"| {_format_pct(summary.get('actual_6h_up50_rate'))} "
        f"| {_format_pct(summary.get('avg_prob_24h_up100'))} "
        f"| {_format_pct(summary.get('actual_24h_up100_rate'))} "
        f"| {_format_pct(summary.get('avg_risk_6h_dd30'))} "
        f"| {_format_pct(summary.get('actual_6h_dd30_rate'))} |"
    )


def _has_usable_outcome(row: Mapping[str, Any]) -> bool:
    return (
        safe_int(row.get("sample_count_2h")) >= MIN_OUTCOME_SAMPLE_2H
        or safe_int(row.get("sample_count_6h")) >= MIN_OUTCOME_SAMPLE_6H
        or safe_int(row.get("sample_count_24h")) >= MIN_OUTCOME_SAMPLE_24H
    )


def _decision_from_row(row: Mapping[str, Any]) -> SignalDecision:
    observed_at = _row_observed_at(row) or utcnow()
    return SignalDecision(
        observed_at=observed_at,
        strategy_version=str(row.get("strategy_version") or "unknown"),
        score=safe_int(row.get("score")),
        pair_state=str(row.get("pair_state") or "watching"),
        should_alert=bool(safe_int(row.get("should_alert"))),
        reasons=tuple(_list_from_json(row.get("reasons"))),
        risk_flags=tuple(_list_from_json(row.get("risk_flags"))),
        features=_mapping_from_json(row.get("feature_json")),
    )


def _row_observed_at(row: Mapping[str, Any]) -> datetime | None:
    value = row.get("observed_at")
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = parse_datetime(str(value)) if value else None
        except ValueError:
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_identity(row: Mapping[str, Any]) -> str:
    pair_address = str(row.get("pair_address") or "").lower()
    token_address = str(row.get("token_address") or "").lower()
    return pair_address or token_address


def _opportunity_bucket(score: int) -> str:
    if score >= 70:
        return "70+"
    if score >= 55:
        return "55-69"
    if score >= 45:
        return "45-54"
    return "<45"


def _quality_flags(row: Mapping[str, Any]) -> list[str]:
    raw = row.get("quality_flags_json")
    parsed = json_loads(str(raw), []) if raw is not None else []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def _mapping_from_json(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    parsed = json_loads(str(value), {}) if value is not None else {}
    return parsed if isinstance(parsed, dict) else {}


def _list_from_json(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    parsed = json_loads(str(value), []) if value is not None else []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def _avg(values: list[float | int | None]) -> float | None:
    clean_values = [float(value) for value in values if value is not None]
    if not clean_values:
        return None
    return round(sum(clean_values) / len(clean_values), 4)


def _rate(hits: int, samples: int) -> float | None:
    if samples <= 0:
        return None
    return round(hits / samples, 4)


def _format_pct(value: Any) -> str:
    numeric = safe_float(value)
    if numeric is None:
        return "--"
    return f"{numeric * 100:.2f}%"


def _format_number(value: Any) -> str:
    numeric = safe_float(value)
    if numeric is None:
        return "--"
    return f"{numeric:.1f}"
