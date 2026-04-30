from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Protocol

from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.utils import isoformat_utc, json_loads, parse_datetime, safe_float, utcnow


class RiskProvider(Protocol):
    provider_name: str

    def fetch_risk_payload(self, token_address: str) -> Mapping[str, Any] | None:
        ...


class FixtureRiskProvider:
    provider_name = "fixture"

    def __init__(self, payloads: Mapping[str, Mapping[str, Any]]) -> None:
        self._payloads = {str(key).lower(): value for key, value in payloads.items()}

    @classmethod
    def from_json_file(cls, path: str | Path) -> "FixtureRiskProvider":
        raw = Path(path).read_text(encoding="utf-8")
        payloads = json_loads(raw, {})
        if not isinstance(payloads, dict):
            payloads = {}
        return cls(payloads)

    def fetch_risk_payload(self, token_address: str) -> Mapping[str, Any] | None:
        payload = self._payloads.get(token_address.lower())
        if payload is None:
            return None
        if payload.get("_error"):
            raise RuntimeError(str(payload["_error"]))
        return payload


def normalize_risk_payload(
    *,
    provider: str,
    token_address: str,
    payload: Mapping[str, Any],
    fetched_at: datetime,
    ttl_hours: int,
) -> dict[str, Any]:
    normalized = {
        "holder_concentration_pct": safe_float(payload.get("holder_concentration_pct")),
        "liquidity_locked": _optional_bool(payload.get("liquidity_locked")),
        "owner_renounced": _optional_bool(payload.get("owner_renounced")),
        "buy_tax_pct": safe_float(payload.get("buy_tax_pct")),
        "sell_tax_pct": safe_float(payload.get("sell_tax_pct")),
    }
    return {
        "provider": provider,
        "token_address": token_address,
        "fetched_at": isoformat_utc(fetched_at),
        "expires_at": isoformat_utc(fetched_at + timedelta(hours=max(1, ttl_hours))),
        "status": "ok",
        "risk_level": str(payload.get("risk_level") or _infer_risk_level(normalized)),
        "confidence": safe_float(payload.get("confidence")),
        "failure_reason": None,
        "normalized": normalized,
        "raw": dict(payload),
    }


def build_risk_failure_snapshot(
    *,
    provider: str,
    token_address: str,
    failure_reason: str,
    fetched_at: datetime,
    ttl_hours: int,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "token_address": token_address,
        "fetched_at": isoformat_utc(fetched_at),
        "expires_at": isoformat_utc(fetched_at + timedelta(hours=max(1, ttl_hours))),
        "status": "failure",
        "risk_level": "unknown",
        "confidence": None,
        "failure_reason": failure_reason,
        "normalized": {},
        "raw": {},
    }


def refresh_risk_snapshots(
    repo: MonitorRepository,
    token_addresses: list[str],
    *,
    providers: list[RiskProvider],
    now: datetime | None = None,
    ttl_hours: int = 6,
) -> dict[str, int]:
    current_time = now or utcnow()
    result = {"updated": 0, "failed": 0, "skipped_current": 0, "no_coverage": 0}
    for provider in providers:
        for token_address in token_addresses:
            current = repo.get_latest_risk_snapshot(token_address, provider=provider.provider_name)
            expires_at = parse_datetime(current.get("expires_at")) if current else None
            if expires_at is not None and expires_at > current_time:
                result["skipped_current"] += 1
                continue
            try:
                payload = provider.fetch_risk_payload(token_address)
            except Exception as exc:
                snapshot = build_risk_failure_snapshot(
                    provider=provider.provider_name,
                    token_address=token_address,
                    failure_reason=f"{type(exc).__name__}: {exc}",
                    fetched_at=current_time,
                    ttl_hours=ttl_hours,
                )
                repo.insert_risk_snapshot(snapshot)
                result["failed"] += 1
                continue
            if payload is None:
                snapshot = build_risk_failure_snapshot(
                    provider=provider.provider_name,
                    token_address=token_address,
                    failure_reason="no_coverage",
                    fetched_at=current_time,
                    ttl_hours=ttl_hours,
                )
                repo.insert_risk_snapshot(snapshot)
                result["no_coverage"] += 1
                continue
            repo.insert_risk_snapshot(
                normalize_risk_payload(
                    provider=provider.provider_name,
                    token_address=token_address,
                    payload=payload,
                    fetched_at=current_time,
                    ttl_hours=ttl_hours,
                )
            )
            result["updated"] += 1
    return result


def _infer_risk_level(normalized: Mapping[str, Any]) -> str:
    holder_concentration = safe_float(normalized.get("holder_concentration_pct"))
    sell_tax = safe_float(normalized.get("sell_tax_pct"))
    buy_tax = safe_float(normalized.get("buy_tax_pct"))
    owner_renounced = normalized.get("owner_renounced")
    if (
        (holder_concentration is not None and holder_concentration >= 0.50)
        or (sell_tax is not None and sell_tax >= 0.15)
        or (buy_tax is not None and buy_tax >= 0.15)
        or owner_renounced is False
    ):
        return "high"
    if (
        (holder_concentration is not None and holder_concentration >= 0.25)
        or (sell_tax is not None and sell_tax >= 0.05)
        or (buy_tax is not None and buy_tax >= 0.05)
    ):
        return "medium"
    return "low"


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "locked", "renounced"}
    return bool(value)
