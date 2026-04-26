from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
REQUEST_HEADERS = {"accept": "application/json", "User-Agent": "token-meme-monitor/1.0"}


class GeckoTerminalClient:
    def __init__(self, network: str = "bsc", timeout_seconds: int = 20) -> None:
        self._network = network
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()

    def fetch_pool_lookback_returns(
        self,
        pool_address: str,
        *,
        observed_at: datetime,
    ) -> dict[str, float | None]:
        rows = self._fetch_ohlcv(
            pool_address,
            timeframe="hour",
            aggregate=1,
            limit=48,
            before_timestamp=int(observed_at.timestamp()) + 3600,
        )
        return compute_lookback_returns(rows, observed_at=observed_at)

    def fetch_pool_ohlcv(
        self,
        pool_address: str,
        *,
        timeframe: str,
        aggregate: int,
        limit: int,
        before_timestamp: int | None = None,
    ) -> list[dict[str, float]]:
        return self._fetch_ohlcv(
            pool_address,
            timeframe=timeframe,
            aggregate=aggregate,
            limit=limit,
            before_timestamp=before_timestamp,
        )

    def _fetch_ohlcv(
        self,
        pool_address: str,
        *,
        timeframe: str,
        aggregate: int,
        limit: int,
        before_timestamp: int | None = None,
    ) -> list[dict[str, float]]:
        path = f"/networks/{self._network}/pools/{pool_address}/ohlcv/{timeframe}?aggregate={aggregate}&limit={limit}"
        if before_timestamp is not None:
            path += f"&before_timestamp={before_timestamp}"
        response = self._session.get(
            f"{GECKO_BASE}{path}",
            timeout=self._timeout_seconds,
            headers=REQUEST_HEADERS,
        )
        response.raise_for_status()
        payload = response.json()
        page = (((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        rows: list[dict[str, float]] = []
        for ts, open_, high, low, close, volume in page:
            rows.append(
                {
                    "ts": int(ts),
                    "open": float(open_),
                    "high": float(high),
                    "low": float(low),
                    "close": float(close),
                    "volume": float(volume),
                }
            )
        return sorted(rows, key=lambda item: item["ts"])


def compute_lookback_returns(rows: list[dict[str, float]], *, observed_at: datetime) -> dict[str, float | None]:
    observed_ts = int(observed_at.astimezone(timezone.utc).timestamp())
    current_row = _latest_before(rows, observed_ts)
    current_price = current_row.get("close") if current_row else None
    if current_price is None or current_price <= 0:
        return {
            "external_return_2h": None,
            "external_return_24h": None,
        }
    return {
        "external_return_2h": _return_from_cutoff(rows, current_price=current_price, cutoff_ts=observed_ts - 2 * 3600),
        "external_return_24h": _return_from_cutoff(rows, current_price=current_price, cutoff_ts=observed_ts - 24 * 3600),
    }


def _return_from_cutoff(rows: list[dict[str, float]], *, current_price: float, cutoff_ts: int) -> float | None:
    previous_row = _latest_before(rows, cutoff_ts)
    if not previous_row:
        return None
    previous_price = previous_row.get("close")
    if previous_price is None or previous_price <= 0:
        return None
    return current_price / previous_price - 1


def _latest_before(rows: list[dict[str, float]], cutoff_ts: int) -> dict[str, float] | None:
    candidates = [row for row in rows if int(row.get("ts") or 0) <= cutoff_ts]
    if not candidates:
        return None
    return candidates[-1]
