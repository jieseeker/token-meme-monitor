from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any

from token_meme_monitor.utils import parse_datetime, safe_float


def market_cap_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 1_000_000:
        return "<1M"
    if value < 10_000_000:
        return "1M-10M"
    if value < 50_000_000:
        return "10M-50M"
    return "50M+"


def compute_candidate_indicators(
    *,
    observed_at: datetime,
    price_usd: float | None,
    volume_h1: float | None,
    market_cap: float | None,
    fdv: float | None,
    history_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    hourly_rows = _collapse_to_hourly(history_rows)
    ordered_rows = _normalize_history_rows(history_rows)
    current_market_proxy = market_cap if market_cap not in (None, 0) else fdv
    return {
        "candidate_indicator_version": "c1",
        "market_cap_bucket": market_cap_bucket(current_market_proxy),
        "volume_impulse_vs_prev24h": _volume_impulse(
            current_value=safe_float(volume_h1),
            observed_at=observed_at,
            history_rows=hourly_rows,
            lookback_hours=24,
        ),
        "volume_impulse_vs_prev72h": _volume_impulse(
            current_value=safe_float(volume_h1),
            observed_at=observed_at,
            history_rows=hourly_rows,
            lookback_hours=72,
        ),
        "h1_return_live": _lookback_return(
            current_value=safe_float(price_usd),
            observed_at=observed_at,
            history_rows=ordered_rows,
            lookback_hours=1,
        ),
        "h4_return_live": _lookback_return(
            current_value=safe_float(price_usd),
            observed_at=observed_at,
            history_rows=ordered_rows,
            lookback_hours=4,
        ),
        "h24_return_live": _lookback_return(
            current_value=safe_float(price_usd),
            observed_at=observed_at,
            history_rows=ordered_rows,
            lookback_hours=24,
        ),
    }


def _normalize_history_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        observed_at = row.get("observed_at")
        dt = observed_at if isinstance(observed_at, datetime) else parse_datetime(observed_at)
        if dt is None:
            continue
        normalized.append(
            {
                "observed_at": dt,
                "price_usd": safe_float(row.get("price_usd")),
                "volume_h1": safe_float(row.get("volume_h1")),
                "market_cap": safe_float(row.get("market_cap")),
                "fdv": safe_float(row.get("fdv")),
            }
        )
    return sorted(normalized, key=lambda row: row["observed_at"])


def _collapse_to_hourly(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_hour: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        observed_at = row.get("observed_at")
        dt = observed_at if isinstance(observed_at, datetime) else parse_datetime(observed_at)
        if dt is None:
            continue
        hour_key = dt.replace(minute=0, second=0, microsecond=0)
        by_hour[hour_key] = {
            "observed_at": dt,
            "price_usd": safe_float(row.get("price_usd")),
            "volume_h1": safe_float(row.get("volume_h1")),
            "market_cap": safe_float(row.get("market_cap")),
            "fdv": safe_float(row.get("fdv")),
        }
    ordered_hours = sorted(by_hour.keys())
    return [by_hour[hour] for hour in ordered_hours]


def _volume_impulse(
    *,
    current_value: float | None,
    observed_at: datetime,
    history_rows: list[dict[str, Any]],
    lookback_hours: int,
) -> float | None:
    if current_value is None or current_value <= 0:
        return None
    cutoff = observed_at - timedelta(hours=lookback_hours)
    baseline = [
        row["volume_h1"]
        for row in history_rows
        if row["observed_at"] < observed_at and row["observed_at"] >= cutoff and row["volume_h1"] not in (None, 0)
    ]
    if len(baseline) < max(4, lookback_hours // 6):
        return None
    baseline_median = median(baseline)
    if baseline_median <= 0:
        return None
    return current_value / baseline_median


def _lookback_return(
    *,
    current_value: float | None,
    observed_at: datetime,
    history_rows: list[dict[str, Any]],
    lookback_hours: int,
) -> float | None:
    if current_value is None or current_value <= 0:
        return None
    cutoff = observed_at - timedelta(hours=lookback_hours)
    candidates = [
        row for row in history_rows if row["observed_at"] <= cutoff and row["price_usd"] not in (None, 0)
    ]
    if not candidates:
        return None
    previous = candidates[-1]["price_usd"]
    if previous is None or previous <= 0:
        return None
    return current_value / previous - 1
