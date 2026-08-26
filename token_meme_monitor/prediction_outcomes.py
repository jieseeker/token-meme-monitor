from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import requests

from token_meme_monitor.clients.geckoterminal import GeckoTerminalClient
from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.utils import json_loads, safe_float, utcnow

OUTCOME_OHLCV_LIMIT = 48
OUTCOME_BEFORE_MARGIN_HOURS = 25
MIN_OUTCOME_SAMPLE_2H = 2
MIN_OUTCOME_SAMPLE_6H = 5
MIN_OUTCOME_SAMPLE_24H = 18


def compute_prediction_outcome_with_hourly_ohlcv(
    repo: MonitorRepository,
    *,
    pair_address: str,
    observed_at: datetime,
    feature_json: str | None,
    network: str,
    gecko_client: GeckoTerminalClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if _as_utc(now or utcnow()) < _as_utc(observed_at) + timedelta(hours=OUTCOME_BEFORE_MARGIN_HOURS):
        return None
    gecko_client = gecko_client or GeckoTerminalClient(network=network)
    base_price = _feature_price(feature_json)
    rows = _cached_or_fetch_hourly_ohlcv(
        repo,
        gecko_client=gecko_client,
        network=network,
        pair_address=pair_address,
        observed_at=observed_at,
    )
    if rows is not None:
        outcome = compute_prediction_outcome_from_ohlcv(rows, observed_at=observed_at, base_price=base_price)
        if outcome_has_usable_samples(outcome):
            return outcome
        fallback = repo.compute_prediction_outcome(pair_address, observed_at)
        if outcome_has_usable_samples(fallback):
            return fallback
        return outcome if outcome_has_samples(outcome) else (fallback if outcome_has_samples(fallback) else None)

    fallback = repo.compute_prediction_outcome(pair_address, observed_at)
    return fallback if outcome_has_samples(fallback) else None


def compute_prediction_outcome_from_ohlcv(
    rows: list[dict[str, Any]],
    *,
    observed_at: datetime,
    base_price: float | None,
) -> dict[str, Any]:
    observed_at = _as_utc(observed_at)
    observed_ts = int(observed_at.timestamp())
    normalized_rows = _normalize_ohlcv_rows(rows)
    signal_base_price = base_price if base_price is not None and base_price > 0 else None
    base_row = _latest_before_or_at(normalized_rows, observed_ts)
    gecko_base_close = base_row["close"] if base_row else None
    effective_base_price = signal_base_price or gecko_base_close
    base_price_source = "signal_feature_price" if signal_base_price is not None else "geckoterminal_close"
    price_divergence_pct = _price_divergence_pct(signal_base_price, gecko_base_close)
    if effective_base_price is None or effective_base_price <= 0:
        return empty_prediction_outcome()

    def window(hours: int) -> list[dict[str, float]]:
        cutoff_ts = observed_ts + hours * 3600
        return [row for row in normalized_rows if observed_ts < row["ts"] <= cutoff_ts]

    values_2h = window(2)
    values_6h = window(6)
    values_24h = window(24)
    max_return_2h = _max_return(values_2h, effective_base_price)
    max_return_6h = _max_return(values_6h, effective_base_price)
    max_return_24h = _max_return(values_24h, effective_base_price)
    min_return_6h = _min_return(values_6h, effective_base_price)
    quality_flags = _quality_flags(
        signal_base_price=signal_base_price,
        gecko_base_close=gecko_base_close,
        price_divergence_pct=price_divergence_pct,
        sample_count_2h=len(values_2h),
        sample_count_6h=len(values_6h),
        sample_count_24h=len(values_24h),
    )
    return {
        "outcome_source": "geckoterminal_hourly",
        "base_price_source": base_price_source,
        "base_price_usd": effective_base_price,
        "gecko_base_close_usd": gecko_base_close,
        "price_divergence_pct": price_divergence_pct,
        "quality_flags": quality_flags,
        "max_return_2h": max_return_2h,
        "max_return_6h": max_return_6h,
        "max_return_24h": max_return_24h,
        "min_return_6h": min_return_6h,
        "hit_2h_up20": 1 if max_return_2h is not None and max_return_2h >= 0.20 else 0,
        "hit_6h_up50": 1 if max_return_6h is not None and max_return_6h >= 0.50 else 0,
        "hit_24h_up100": 1 if max_return_24h is not None and max_return_24h >= 1.00 else 0,
        "hit_6h_dd30": 1 if min_return_6h is not None and min_return_6h <= -0.30 else 0,
        "sample_count_2h": len(values_2h),
        "sample_count_6h": len(values_6h),
        "sample_count_24h": len(values_24h),
    }


def empty_prediction_outcome() -> dict[str, Any]:
    return {
        "outcome_source": "geckoterminal_hourly",
        "base_price_source": "missing",
        "base_price_usd": None,
        "gecko_base_close_usd": None,
        "price_divergence_pct": None,
        "quality_flags": ["base_price_missing"],
        "max_return_2h": None,
        "max_return_6h": None,
        "max_return_24h": None,
        "min_return_6h": None,
        "hit_2h_up20": 0,
        "hit_6h_up50": 0,
        "hit_24h_up100": 0,
        "hit_6h_dd30": 0,
        "sample_count_2h": 0,
        "sample_count_6h": 0,
        "sample_count_24h": 0,
    }


def outcome_has_samples(outcome: dict[str, Any]) -> bool:
    return any(int(outcome.get(field) or 0) > 0 for field in ("sample_count_2h", "sample_count_6h", "sample_count_24h"))


def outcome_has_usable_samples(outcome: Mapping[str, Any]) -> bool:
    return (
        int(outcome.get("sample_count_2h") or 0) >= MIN_OUTCOME_SAMPLE_2H
        or int(outcome.get("sample_count_6h") or 0) >= MIN_OUTCOME_SAMPLE_6H
        or int(outcome.get("sample_count_24h") or 0) >= MIN_OUTCOME_SAMPLE_24H
    )


def _cached_or_fetch_hourly_ohlcv(
    repo: MonitorRepository,
    *,
    gecko_client: GeckoTerminalClient,
    network: str,
    pair_address: str,
    observed_at: datetime,
) -> list[dict[str, Any]] | None:
    before_timestamp = _outcome_before_timestamp(observed_at)
    cached_rows = repo.list_external_ohlcv(
        network=network,
        pool_address=pair_address,
        timeframe="hour",
        aggregate=1,
        before_timestamp=before_timestamp,
        limit=OUTCOME_OHLCV_LIMIT,
    )
    recorded_count = repo.get_external_ohlcv_fetch_row_count(
        network=network,
        pool_address=pair_address,
        timeframe="hour",
        aggregate=1,
        limit=OUTCOME_OHLCV_LIMIT,
        before_timestamp=before_timestamp,
    )
    if recorded_count is not None and len(cached_rows) >= recorded_count:
        return cached_rows[-recorded_count:] if recorded_count else []
    if _cached_rows_cover_outcome(cached_rows, observed_at):
        return cached_rows

    try:
        fetched_rows = gecko_client.fetch_pool_ohlcv(
            pair_address,
            timeframe="hour",
            aggregate=1,
            limit=OUTCOME_OHLCV_LIMIT,
            before_timestamp=before_timestamp,
        )
    except requests.RequestException:
        return None
    repo.upsert_external_ohlcv(
        network=network,
        pool_address=pair_address,
        timeframe="hour",
        aggregate=1,
        rows=fetched_rows,
    )
    repo.record_external_ohlcv_fetch(
        network=network,
        pool_address=pair_address,
        timeframe="hour",
        aggregate=1,
        limit=OUTCOME_OHLCV_LIMIT,
        before_timestamp=before_timestamp,
        row_count=len(fetched_rows),
    )
    return fetched_rows


def _outcome_before_timestamp(observed_at: datetime) -> int:
    return int((_as_utc(observed_at) + timedelta(hours=OUTCOME_BEFORE_MARGIN_HOURS)).timestamp())


def _cached_rows_cover_outcome(rows: list[dict[str, Any]], observed_at: datetime) -> bool:
    if not rows:
        return False
    observed_ts = int(_as_utc(observed_at).timestamp())
    end_ts = observed_ts + 24 * 3600
    timestamps = [int(row.get("ts") or 0) for row in rows]
    return any(ts <= observed_ts for ts in timestamps) and any(ts <= end_ts and ts > observed_ts for ts in timestamps)


def _normalize_ohlcv_rows(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    normalized: list[dict[str, float]] = []
    for row in rows:
        ts = safe_float(row.get("ts"))
        high = safe_float(row.get("high"))
        low = safe_float(row.get("low"))
        close = safe_float(row.get("close"))
        if ts is None or high is None or low is None or close is None:
            continue
        if high <= 0 or low <= 0 or close <= 0:
            continue
        normalized.append({"ts": int(ts), "high": high, "low": low, "close": close})
    return sorted(normalized, key=lambda row: row["ts"])


def _latest_before_or_at(rows: list[dict[str, float]], observed_ts: int) -> dict[str, float] | None:
    candidates = [row for row in rows if row["ts"] <= observed_ts]
    return candidates[-1] if candidates else None


def _max_return(rows: list[dict[str, float]], base_price: float) -> float | None:
    return max(row["high"] for row in rows) / base_price - 1 if rows else None


def _min_return(rows: list[dict[str, float]], base_price: float) -> float | None:
    return min(row["low"] for row in rows) / base_price - 1 if rows else None


def _price_divergence_pct(signal_base_price: float | None, gecko_base_close: float | None) -> float | None:
    if signal_base_price is None or gecko_base_close is None or gecko_base_close <= 0:
        return None
    return signal_base_price / gecko_base_close - 1


def _quality_flags(
    *,
    signal_base_price: float | None,
    gecko_base_close: float | None,
    price_divergence_pct: float | None,
    sample_count_2h: int,
    sample_count_6h: int,
    sample_count_24h: int,
) -> list[str]:
    flags: list[str] = []
    if signal_base_price is None:
        flags.append("base_price_fallback_gecko_close")
    if gecko_base_close is None:
        flags.append("gecko_base_close_missing")
    if price_divergence_pct is not None and abs(price_divergence_pct) > 0.10:
        flags.append("price_source_divergence_gt_10pct")
    if sample_count_2h < MIN_OUTCOME_SAMPLE_2H:
        flags.append("partial_2h_ohlcv")
    if sample_count_6h < MIN_OUTCOME_SAMPLE_6H:
        flags.append("partial_6h_ohlcv")
    if sample_count_24h < MIN_OUTCOME_SAMPLE_24H:
        flags.append("partial_24h_ohlcv")
    return flags


def _feature_price(feature_json: str | None) -> float | None:
    features = json_loads(feature_json, {})
    if not isinstance(features, dict):
        return None
    return safe_float(features.get("price_usd"))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
