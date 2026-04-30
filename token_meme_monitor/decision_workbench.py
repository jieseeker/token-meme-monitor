from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Any, Mapping

from token_meme_monitor.utils import json_loads, parse_datetime, safe_float, safe_int, utcnow


def build_decision_case(row: Mapping[str, Any], *, note: Mapping[str, Any] | None = None) -> dict[str, Any]:
    signal_id = row.get("signal_id")
    case_id = f"signal:{signal_id}" if signal_id not in (None, "") else f"pair:{row.get('pair_address')}"
    observed_at = row.get("observed_at")
    prediction = {
        "prob_2h_up20": safe_float(row.get("prob_2h_up20")),
        "prob_6h_up50": safe_float(row.get("prob_6h_up50")),
        "prob_24h_up100": safe_float(row.get("prob_24h_up100")),
        "risk_6h_dd30": safe_float(row.get("risk_6h_dd30")),
        "short_momentum_score": safe_int(_first_present(row.get("short_momentum_score"), row.get("opportunity_score"))),
        "continuation_score": safe_int(row.get("continuation_score")),
        "breakout_score": safe_int(row.get("breakout_score")),
        "stage": row.get("stage"),
        "reasons": _json_list(row.get("prediction_reasons")),
    }
    outcome = {
        "hit_2h_up20": safe_int(row.get("hit_2h_up20")),
        "hit_6h_up50": safe_int(row.get("hit_6h_up50")),
        "hit_24h_up100": safe_int(row.get("hit_24h_up100")),
        "max_return_2h": safe_float(row.get("max_return_2h")),
        "max_return_6h": safe_float(row.get("max_return_6h")),
        "max_return_24h": safe_float(row.get("max_return_24h")),
        "min_return_6h": safe_float(row.get("min_return_6h")),
        "sample_count_2h": safe_int(row.get("sample_count_2h")),
        "sample_count_6h": safe_int(row.get("sample_count_6h")),
        "sample_count_24h": safe_int(row.get("sample_count_24h")),
    }
    return {
        "case_id": case_id,
        "signal_id": signal_id,
        "pair_address": row.get("pair_address"),
        "token_address": row.get("token_address"),
        "token_symbol": row.get("token_symbol"),
        "token_name": row.get("token_name"),
        "observed_at": observed_at,
        "signal": {
            "score": safe_int(row.get("score")),
            "pair_state": row.get("pair_state"),
            "reasons": _json_list(row.get("reasons")),
            "risk_flags": _json_list(row.get("risk_flags")),
            "features": _json_dict(row.get("feature_json")),
        },
        "prediction": prediction,
        "outcome": outcome,
        "timeline": _timeline(observed_at, prediction, outcome),
        "note": dict(note or {"note": "", "watchlisted": False}),
    }


def build_decision_queues(rows: list[Mapping[str, Any]], *, now: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    current_time = now or utcnow()
    cases = [build_decision_case(row) for row in rows]
    high_confidence = [
        case for case in cases if safe_int(case["prediction"].get("short_momentum_score")) >= 70
    ]
    missed_prediction = [
        case
        for case in high_confidence
        if case["outcome"].get("sample_count_2h", 0) > 0 and safe_int(case["outcome"].get("hit_2h_up20")) == 0
    ]
    strong_win = [
        case
        for case in cases
        if (safe_float(case["outcome"].get("max_return_2h")) or 0.0) >= 0.20
        or (safe_float(case["outcome"].get("max_return_24h")) or 0.0) >= 1.00
    ]
    stale_data = []
    for case in cases:
        observed_at = parse_datetime(str(case.get("observed_at"))) if case.get("observed_at") else None
        if observed_at is None:
            continue
        has_outcome = any(
            safe_int(case["outcome"].get(field)) > 0
            for field in ("sample_count_2h", "sample_count_6h", "sample_count_24h")
        )
        if not has_outcome and observed_at <= current_time - timedelta(hours=25):
            stale_data.append(case)
    return {
        "high_confidence": sorted(high_confidence, key=lambda case: safe_int(case["prediction"].get("short_momentum_score")), reverse=True),
        "missed_prediction": missed_prediction,
        "strong_win": sorted(strong_win, key=lambda case: safe_float(case["outcome"].get("max_return_2h")) or 0.0, reverse=True),
        "stale_data": stale_data,
    }


def export_cases_csv(cases: list[Mapping[str, Any]]) -> str:
    output = io.StringIO()
    fieldnames = [
        "case_id",
        "signal_id",
        "pair_address",
        "token_address",
        "token_symbol",
        "observed_at",
        "signal_score",
        "short_momentum_score",
        "max_return_2h",
        "max_return_24h",
        "watchlisted",
        "note",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for case in cases:
        writer.writerow(
            {
                "case_id": case.get("case_id"),
                "signal_id": case.get("signal_id"),
                "pair_address": case.get("pair_address"),
                "token_address": case.get("token_address"),
                "token_symbol": case.get("token_symbol"),
                "observed_at": case.get("observed_at"),
                "signal_score": (case.get("signal") or {}).get("score"),
                "short_momentum_score": (case.get("prediction") or {}).get("short_momentum_score"),
                "max_return_2h": (case.get("outcome") or {}).get("max_return_2h"),
                "max_return_24h": (case.get("outcome") or {}).get("max_return_24h"),
                "watchlisted": (case.get("note") or {}).get("watchlisted", False),
                "note": (case.get("note") or {}).get("note", ""),
            }
        )
    return output.getvalue()


def _timeline(observed_at: Any, prediction: Mapping[str, Any], outcome: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = [{"event": "signal_observed", "at": observed_at}]
    if prediction.get("short_momentum_score") is not None:
        items.append({"event": "prediction_written", "at": observed_at, "score": prediction.get("short_momentum_score")})
    if any(safe_int(outcome.get(field)) > 0 for field in ("sample_count_2h", "sample_count_6h", "sample_count_24h")):
        items.append({"event": "outcome_observed", "at": None, "max_return_2h": outcome.get("max_return_2h")})
    return items


def _json_dict(value: Any) -> dict[str, Any]:
    parsed = json_loads(value, {}) if isinstance(value, str) else value
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[str]:
    parsed = json_loads(value, []) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item not in (None, "")]


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None
