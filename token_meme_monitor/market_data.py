from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Any, Mapping

from token_meme_monitor.models import PairSnapshot
from token_meme_monitor.utils import safe_float

MAX_PRICE_USD = 10_000_000.0
MAX_PRICE_NATIVE = 10_000_000.0
MAX_USD_NOTIONAL = 10_000_000_000_000.0
MAX_PRICE_CHANGE_PERCENT = 1_000_000.0


def sanitize_pair_snapshot(
    snapshot: PairSnapshot,
    *,
    monitor_universe: str,
    alpha_reference: Mapping[str, Any] | None = None,
) -> PairSnapshot:
    flags: list[str] = []
    field_sources: dict[str, str] = {}
    alpha_reference = alpha_reference or {}

    alpha_price = _bounded(safe_float(alpha_reference.get("price")), minimum=0.0, maximum=MAX_PRICE_USD, allow_zero=False)
    alpha_market_cap = _bounded(
        safe_float(alpha_reference.get("market_cap")),
        minimum=0.0,
        maximum=MAX_USD_NOTIONAL,
        allow_zero=False,
    )
    alpha_fdv = _bounded(safe_float(alpha_reference.get("fdv")), minimum=0.0, maximum=MAX_USD_NOTIONAL, allow_zero=False)
    alpha_liquidity = _bounded(
        safe_float(alpha_reference.get("liquidity")),
        minimum=0.0,
        maximum=MAX_USD_NOTIONAL,
        allow_zero=True,
    )
    alpha_volume_24h = _bounded(
        safe_float(alpha_reference.get("volume_24h")),
        minimum=0.0,
        maximum=MAX_USD_NOTIONAL,
        allow_zero=True,
    )

    price_usd = _canonical_price_field(
        snapshot.price_usd,
        alpha_price,
        flags,
        field_sources,
        maximum=MAX_PRICE_USD,
    )
    market_cap = _canonical_usd_field(
        "market_cap",
        snapshot.market_cap,
        alpha_market_cap,
        flags,
        field_sources,
        prefer_reference=monitor_universe == "binance_alpha",
        ratio_limit=50.0,
        maximum=MAX_USD_NOTIONAL,
    )
    fdv = _canonical_usd_field(
        "fdv",
        snapshot.fdv,
        alpha_fdv,
        flags,
        field_sources,
        prefer_reference=monitor_universe == "binance_alpha",
        ratio_limit=50.0,
        maximum=MAX_USD_NOTIONAL,
    )
    liquidity_usd = _canonical_usd_field(
        "liquidity_usd",
        snapshot.liquidity_usd,
        alpha_liquidity,
        flags,
        field_sources,
        prefer_reference=False,
        ratio_limit=100.0,
        maximum=MAX_USD_NOTIONAL,
        allow_zero=True,
    )
    volume_h24 = _canonical_usd_field(
        "volume_h24",
        snapshot.volume_h24,
        alpha_volume_24h,
        flags,
        field_sources,
        prefer_reference=False,
        ratio_limit=100.0,
        maximum=MAX_USD_NOTIONAL,
        allow_zero=True,
    )

    price_native = _bounded(
        snapshot.price_native,
        minimum=0.0,
        maximum=MAX_PRICE_NATIVE,
        allow_zero=False,
    )
    if snapshot.price_native is not None and price_native is None:
        flags.append("price_native_invalid")

    volume_m5 = _bounded(snapshot.volume_m5, minimum=0.0, maximum=MAX_USD_NOTIONAL, allow_zero=True) or 0.0
    if not _matches(snapshot.volume_m5, volume_m5):
        flags.append("volume_m5_invalid")

    volume_h1 = _bounded(snapshot.volume_h1, minimum=0.0, maximum=MAX_USD_NOTIONAL, allow_zero=True) or 0.0
    if not _matches(snapshot.volume_h1, volume_h1):
        flags.append("volume_h1_invalid")

    price_change_m5 = _bounded(
        snapshot.price_change_m5,
        minimum=-MAX_PRICE_CHANGE_PERCENT,
        maximum=MAX_PRICE_CHANGE_PERCENT,
        allow_zero=True,
    ) or 0.0
    if not _matches(snapshot.price_change_m5, price_change_m5):
        flags.append("price_change_m5_invalid")

    price_change_h1 = _bounded(
        snapshot.price_change_h1,
        minimum=-MAX_PRICE_CHANGE_PERCENT,
        maximum=MAX_PRICE_CHANGE_PERCENT,
        allow_zero=True,
    ) or 0.0
    if not _matches(snapshot.price_change_h1, price_change_h1):
        flags.append("price_change_h1_invalid")

    price_change_h24 = _bounded(
        snapshot.price_change_h24,
        minimum=-MAX_PRICE_CHANGE_PERCENT,
        maximum=MAX_PRICE_CHANGE_PERCENT,
        allow_zero=True,
    ) or 0.0
    if not _matches(snapshot.price_change_h24, price_change_h24):
        flags.append("price_change_h24_invalid")

    raw_payload = dict(snapshot.raw_payload)
    raw_payload["_data_quality"] = {
        "flags": sorted(set(flags)),
        "sources": field_sources,
    }

    return replace(
        snapshot,
        price_usd=price_usd,
        price_native=price_native,
        liquidity_usd=liquidity_usd,
        fdv=fdv,
        market_cap=market_cap,
        volume_m5=volume_m5,
        volume_h1=volume_h1,
        volume_h24=volume_h24,
        price_change_m5=price_change_m5,
        price_change_h1=price_change_h1,
        price_change_h24=price_change_h24,
        raw_payload=raw_payload,
    )


def sanitize_alpha_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    clean = dict(metadata)
    numeric_fields = {
        "alpha_price": (0.0, MAX_PRICE_USD, False),
        "alpha_market_cap": (0.0, MAX_USD_NOTIONAL, False),
        "alpha_fdv": (0.0, MAX_USD_NOTIONAL, False),
        "alpha_liquidity": (0.0, MAX_USD_NOTIONAL, True),
        "alpha_volume_24h": (0.0, MAX_USD_NOTIONAL, True),
    }
    for field, (minimum, maximum, allow_zero) in numeric_fields.items():
        clean[field] = _bounded(safe_float(clean.get(field)), minimum=minimum, maximum=maximum, allow_zero=allow_zero)
    holder_count = clean.get("holder_count")
    try:
        clean["holder_count"] = int(holder_count) if holder_count not in (None, "") else None
    except (TypeError, ValueError):
        clean["holder_count"] = None
    return clean


def build_alpha_reference(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    metadata = metadata or {}
    return {
        "price": metadata.get("alpha_price"),
        "market_cap": metadata.get("alpha_market_cap"),
        "fdv": metadata.get("alpha_fdv"),
        "liquidity": metadata.get("alpha_liquidity"),
        "volume_24h": metadata.get("alpha_volume_24h"),
    }


def _canonical_usd_field(
    field_name: str,
    snapshot_value: Any,
    reference_value: float | None,
    flags: list[str],
    field_sources: dict[str, str],
    *,
    prefer_reference: bool,
    ratio_limit: float,
    maximum: float,
    allow_zero: bool = False,
) -> float | None:
    clean_snapshot = _bounded(snapshot_value, minimum=0.0, maximum=maximum, allow_zero=allow_zero)
    if snapshot_value is not None and clean_snapshot is None:
        flags.append(f"{field_name}_invalid")

    if reference_value is not None and clean_snapshot is not None:
        ratio = _ratio(clean_snapshot, reference_value)
        if ratio is not None and ratio > ratio_limit:
            flags.append(f"{field_name}_outlier_replaced_reference")
            field_sources[field_name] = "alpha_reference"
            return reference_value

    if prefer_reference and reference_value is not None:
        field_sources[field_name] = "alpha_reference"
        return reference_value

    if clean_snapshot is not None:
        field_sources[field_name] = "dexscreener"
        return clean_snapshot

    if reference_value is not None:
        flags.append(f"{field_name}_fallback_reference")
        field_sources[field_name] = "alpha_reference"
        return reference_value

    field_sources[field_name] = "missing"
    return None


def _canonical_price_field(
    snapshot_value: Any,
    reference_value: float | None,
    flags: list[str],
    field_sources: dict[str, str],
    *,
    maximum: float,
) -> float | None:
    clean_snapshot = _bounded(snapshot_value, minimum=0.0, maximum=maximum, allow_zero=False)
    if snapshot_value is not None and clean_snapshot is None:
        flags.append("price_usd_invalid")

    if clean_snapshot is not None:
        divergence_ratio = _ratio(clean_snapshot, reference_value) if reference_value is not None else None
        if divergence_ratio is not None and divergence_ratio > 50.0:
            flags.append("price_usd_reference_divergent")
        field_sources["price_usd"] = "dexscreener"
        return clean_snapshot

    if reference_value is not None:
        flags.append("price_usd_fallback_reference")
        field_sources["price_usd"] = "alpha_reference"
        return reference_value

    field_sources["price_usd"] = "missing"
    return None


def _bounded(
    value: Any,
    *,
    minimum: float | None,
    maximum: float | None,
    allow_zero: bool,
) -> float | None:
    parsed = safe_float(value)
    if parsed is None:
        return None
    if not isfinite(parsed):
        return None
    if minimum is not None:
        if allow_zero and parsed == 0:
            pass
        elif parsed <= minimum:
            return None
    if maximum is not None and parsed > maximum:
        return None
    return parsed


def _ratio(left: float, right: float) -> float | None:
    if left <= 0 or right <= 0:
        return None
    return max(left / right, right / left)


def _matches(original: Any, cleaned: Any) -> bool:
    original_clean = safe_float(original)
    if original_clean is None and cleaned in (None, 0.0):
        return True
    return original_clean == cleaned
