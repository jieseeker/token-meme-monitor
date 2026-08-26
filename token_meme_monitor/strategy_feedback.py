from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from token_meme_monitor.prediction_outcomes import MIN_OUTCOME_SAMPLE_2H, MIN_OUTCOME_SAMPLE_6H, MIN_OUTCOME_SAMPLE_24H
from token_meme_monitor.utils import json_dumps, json_loads, parse_datetime, safe_float, safe_int, utcnow


FEEDBACK_VERSION = "strategy-feedback-v1"
DEFAULT_MIN_SLICE_EVENTS = 30


def build_strategy_feedback_report(
    rows: list[Mapping[str, Any]],
    *,
    min_slice_events: int = DEFAULT_MIN_SLICE_EVENTS,
    max_price_divergence_pct: float | None = 0.10,
    generated_at: Any = None,
) -> dict[str, Any]:
    generated = generated_at or utcnow()
    generated_text = generated.isoformat(timespec="seconds") if hasattr(generated, "isoformat") else str(generated)
    eligible_rows = [row for row in rows if _price_quality_ok(row, max_price_divergence_pct)]
    outcome_rows = [row for row in eligible_rows if _has_usable_outcome(row)]
    observed_times = [parse_datetime(str(row.get("observed_at"))) for row in eligible_rows if row.get("observed_at")]
    observed_times = [value for value in observed_times if value is not None]
    baseline = _metrics(outcome_rows, eligible_rows)
    slices = _build_slices(eligible_rows, baseline=baseline, min_slice_events=min_slice_events)
    recommendations = [
        item["recommendation"]
        for item in slices
        if isinstance(item.get("recommendation"), dict) and item["recommendation"].get("suggested_action")
    ]
    return {
        "generated_at": generated_text,
        "feedback_version": FEEDBACK_VERSION,
        "min_slice_events": min_slice_events,
        "max_price_divergence_pct": max_price_divergence_pct,
        "window_start": min(observed_times).isoformat(timespec="seconds") if observed_times else None,
        "window_end": max(observed_times).isoformat(timespec="seconds") if observed_times else None,
        "summary": {
            "prediction_count": len(rows),
            "eligible_prediction_count": len(eligible_rows),
            "outcome_count": len(outcome_rows),
            "missing_outcome_count": max(0, len(eligible_rows) - len(outcome_rows)),
            "missing_outcome_rate": _rate(max(0, len(eligible_rows) - len(outcome_rows)), len(eligible_rows)),
            "slice_count": len(slices),
            "recommendation_count": len(recommendations),
        },
        "baseline": baseline,
        "slices": slices,
        "recommendations": recommendations,
    }


def compact_strategy_feedback_summary(report: Mapping[str, Any], *, limit: int = 3) -> dict[str, Any]:
    recommendations = list(report.get("recommendations") or [])
    return {
        "generated_at": report.get("generated_at"),
        "feedback_version": report.get("feedback_version"),
        "prediction_count": (report.get("summary") or {}).get("prediction_count", 0),
        "outcome_count": (report.get("summary") or {}).get("outcome_count", 0),
        "missing_outcome_rate": (report.get("summary") or {}).get("missing_outcome_rate"),
        "recommendation_count": len(recommendations),
        "top_recommendations": recommendations[:limit],
    }


def write_strategy_feedback_outputs(report: Mapping[str, Any], *, json_path: str, markdown_path: str) -> None:
    json_output = Path(json_path)
    markdown_output = Path(markdown_path)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json_dumps(report) + "\n", encoding="utf-8")
    markdown_output.write_text(render_strategy_feedback_markdown(report), encoding="utf-8")


def render_strategy_feedback_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    baseline = report.get("baseline") or {}
    lines = [
        "# Strategy Feedback Report",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Predictions: `{summary.get('prediction_count', 0)}`",
        f"- Eligible predictions: `{summary.get('eligible_prediction_count', 0)}`",
        f"- Outcomes: `{summary.get('outcome_count', 0)}`",
        f"- Missing outcome rate: `{_format_pct(summary.get('missing_outcome_rate'))}`",
        f"- Baseline 2h win rate: `{_format_pct(baseline.get('win_rate_2h'))}`",
        "",
        "## Recommendations",
        "",
    ]
    recommendations = report.get("recommendations") or []
    if not recommendations:
        lines.append("- No review-only recommendations met the sample threshold.")
    for item in recommendations:
        evidence = item.get("evidence") or {}
        lines.append(
            f"- `{item.get('dimension')}:{item.get('slice_key')}` "
            f"{item.get('suggested_action')} "
            f"(events={evidence.get('events')}, lift_2h={_format_pct(evidence.get('lift_2h'))}, "
            f"win_2h={_format_pct(evidence.get('win_rate_2h'))})"
        )
    lines.extend(
        [
            "",
            "## Slices",
            "",
            "| Dimension | Slice | Events | Rows | 2h win | 2h lift | 2h calibration error | Missing outcomes |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in report.get("slices") or []:
        metrics = item.get("metrics") or {}
        lines.append(
            f"| {item.get('dimension')} "
            f"| {item.get('slice_key')} "
            f"| {safe_int(metrics.get('events'))} "
            f"| {safe_int(metrics.get('rows'))} "
            f"| {_format_pct(metrics.get('win_rate_2h'))} "
            f"| {_format_pct(metrics.get('lift_2h'))} "
            f"| {_format_pct(metrics.get('calibration_error_2h'))} "
            f"| {_format_pct(metrics.get('missing_outcome_rate'))} |"
        )
    return "\n".join(lines) + "\n"


def _build_slices(
    rows: list[Mapping[str, Any]],
    *,
    baseline: Mapping[str, Any],
    min_slice_events: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        for dimension, slice_key in _slice_keys(row).items():
            grouped[(dimension, slice_key)].append(row)

    output: list[dict[str, Any]] = []
    for (dimension, slice_key), slice_rows in sorted(grouped.items()):
        outcome_rows = [row for row in slice_rows if _has_usable_outcome(row)]
        metrics = _metrics(outcome_rows, slice_rows, baseline=baseline)
        recommendation = _recommendation(dimension, slice_key, metrics, min_slice_events=min_slice_events)
        output.append(
            {
                "dimension": dimension,
                "slice_key": slice_key,
                "metrics": metrics,
                "recommendation": recommendation,
            }
        )
    return output


def _metrics(
    outcome_rows: list[Mapping[str, Any]],
    all_rows: list[Mapping[str, Any]],
    *,
    baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    sample_2h = [row for row in outcome_rows if safe_int(row.get("sample_count_2h")) >= MIN_OUTCOME_SAMPLE_2H]
    sample_6h = [row for row in outcome_rows if safe_int(row.get("sample_count_6h")) >= MIN_OUTCOME_SAMPLE_6H]
    sample_24h = [row for row in outcome_rows if safe_int(row.get("sample_count_24h")) >= MIN_OUTCOME_SAMPLE_24H]
    win_rate_2h = _rate(sum(safe_int(row.get("hit_2h_up20")) for row in sample_2h), len(sample_2h))
    win_rate_6h = _rate(sum(safe_int(row.get("hit_6h_up50")) for row in sample_6h), len(sample_6h))
    win_rate_24h = _rate(sum(safe_int(row.get("hit_24h_up100")) for row in sample_24h), len(sample_24h))
    avg_prob_2h = _avg([safe_float(row.get("prob_2h_up20")) for row in sample_2h])
    avg_prob_6h = _avg([safe_float(row.get("prob_6h_up50")) for row in sample_6h])
    avg_prob_24h = _avg([safe_float(row.get("prob_24h_up100")) for row in sample_24h])
    baseline_win_2h = safe_float((baseline or {}).get("win_rate_2h"))
    lift_2h = None if baseline_win_2h is None or win_rate_2h is None else round(win_rate_2h - baseline_win_2h, 4)
    return {
        "rows": len(all_rows),
        "events": len(outcome_rows),
        "distinct_tokens": len({str(row.get("token_address") or "").lower() for row in all_rows if row.get("token_address")}),
        "missing_outcome_rate": _rate(max(0, len(all_rows) - len(outcome_rows)), len(all_rows)),
        "sample_2h": len(sample_2h),
        "win_rate_2h": win_rate_2h,
        "avg_prob_2h": avg_prob_2h,
        "calibration_error_2h": _abs_delta(avg_prob_2h, win_rate_2h),
        "lift_2h": lift_2h,
        "sample_6h": len(sample_6h),
        "win_rate_6h": win_rate_6h,
        "avg_prob_6h": avg_prob_6h,
        "calibration_error_6h": _abs_delta(avg_prob_6h, win_rate_6h),
        "sample_24h": len(sample_24h),
        "win_rate_24h": win_rate_24h,
        "avg_prob_24h": avg_prob_24h,
        "calibration_error_24h": _abs_delta(avg_prob_24h, win_rate_24h),
    }


def _recommendation(
    dimension: str,
    slice_key: str,
    metrics: Mapping[str, Any],
    *,
    min_slice_events: int,
) -> dict[str, Any]:
    events = safe_int(metrics.get("events"))
    lift_2h = safe_float(metrics.get("lift_2h"))
    if events < min_slice_events or lift_2h is None:
        return {}
    evidence = {
        "events": events,
        "rows": safe_int(metrics.get("rows")),
        "win_rate_2h": metrics.get("win_rate_2h"),
        "lift_2h": lift_2h,
        "calibration_error_2h": metrics.get("calibration_error_2h"),
        "missing_outcome_rate": metrics.get("missing_outcome_rate"),
    }
    if lift_2h >= 0.25:
        return {
            "dimension": dimension,
            "slice_key": slice_key,
            "suggested_action": "review_for_more_weight",
            "evidence": evidence,
            "risk_note": "Review sample freshness before changing thresholds or weights.",
        }
    if lift_2h <= -0.25:
        return {
            "dimension": dimension,
            "slice_key": slice_key,
            "suggested_action": "investigate_or_downweight",
            "evidence": evidence,
            "risk_note": "Do not mutate scoring automatically; inspect recent cases first.",
        }
    return {}


def _slice_keys(row: Mapping[str, Any]) -> dict[str, str]:
    features = _json_dict(row.get("feature_json"))
    return {
        "stage": str(row.get("stage") or "unknown"),
        "score_band": _score_band(_first_number(row.get("short_momentum_score"), row.get("opportunity_score"), row.get("score"))),
        "market_cap_bucket": str(features.get("market_cap_bucket") or _market_cap_bucket(features.get("market_cap"))),
        "liquidity_bucket": _liquidity_bucket(features.get("liquidity_usd")),
    }


def _price_quality_ok(row: Mapping[str, Any], max_price_divergence_pct: float | None) -> bool:
    divergence = safe_float(row.get("price_divergence_pct"))
    return max_price_divergence_pct is None or divergence is None or abs(divergence) <= max_price_divergence_pct


def _has_usable_outcome(row: Mapping[str, Any]) -> bool:
    return (
        safe_int(row.get("sample_count_2h")) >= MIN_OUTCOME_SAMPLE_2H
        or safe_int(row.get("sample_count_6h")) >= MIN_OUTCOME_SAMPLE_6H
        or safe_int(row.get("sample_count_24h")) >= MIN_OUTCOME_SAMPLE_24H
    )


def _score_band(value: Any) -> str:
    parsed = safe_float(value)
    if parsed is None:
        return "unknown"
    if parsed < 45:
        return "<45"
    if parsed < 55:
        return "45-54"
    if parsed < 70:
        return "55-69"
    return "70+"


def _market_cap_bucket(value: Any) -> str:
    parsed = safe_float(value)
    if parsed is None:
        return "unknown"
    if parsed < 500_000:
        return "nano"
    if parsed < 2_000_000:
        return "micro"
    if parsed < 10_000_000:
        return "small"
    return "large"


def _liquidity_bucket(value: Any) -> str:
    parsed = safe_float(value)
    if parsed is None:
        return "unknown"
    if parsed < 25_000:
        return "thin"
    if parsed < 150_000:
        return "normal"
    if parsed < 500_000:
        return "deep"
    return "very_deep"


def _json_dict(value: Any) -> dict[str, Any]:
    parsed = json_loads(value, {}) if isinstance(value, str) else value
    return parsed if isinstance(parsed, dict) else {}


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = safe_float(value)
        if parsed is not None:
            return parsed
    return None


def _avg(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 4)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _abs_delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(abs(left - right), 4)


def _format_pct(value: Any) -> str:
    parsed = safe_float(value)
    if parsed is None:
        return "--"
    return f"{parsed * 100:.2f}%"
