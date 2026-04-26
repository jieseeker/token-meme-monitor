from __future__ import annotations

from typing import Any

import requests


class BinanceMarketClient:
    def __init__(self, timeout_seconds: int = 20) -> None:
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()
        self._headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}

    def fetch_futures_registry(self) -> dict[str, dict[str, list[str]]]:
        registry: dict[str, dict[str, list[str]]] = {}
        for market, url in (
            ("usdm", "https://fapi.binance.com/fapi/v1/exchangeInfo"),
            ("coinm", "https://dapi.binance.com/dapi/v1/exchangeInfo"),
        ):
            response = self._session.get(url, timeout=self._timeout_seconds, headers=self._headers)
            response.raise_for_status()
            payload = response.json()
            symbols = payload.get("symbols") or []
            for item in symbols:
                if not isinstance(item, dict):
                    continue
                base_asset = str(item.get("baseAsset") or "").upper().strip()
                symbol = str(item.get("symbol") or "").upper().strip()
                if not base_asset or not symbol:
                    continue
                if market == "usdm" and item.get("status") not in (None, "TRADING"):
                    continue
                entry = registry.setdefault(base_asset, {"usdm": [], "coinm": []})
                entry[market].append(symbol)
        for entry in registry.values():
            entry["usdm"] = sorted(set(entry["usdm"]))
            entry["coinm"] = sorted(set(entry["coinm"]))
        return registry


def build_binance_listing_labels(
    *,
    symbol: str | None,
    cex_coin_name: str | None,
    listing_cex: bool | None,
    futures_registry: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    lookup_symbol = (cex_coin_name or symbol or "").upper().strip()
    futures_info = futures_registry.get(lookup_symbol, {})
    usdm_contracts = list(futures_info.get("usdm") or [])
    coinm_contracts = list(futures_info.get("coinm") or [])
    return {
        "binance_cex_listed": bool(listing_cex),
        "binance_cex_symbol": lookup_symbol or None,
        "binance_futures_usdm_listed": bool(usdm_contracts),
        "binance_futures_coinm_listed": bool(coinm_contracts),
        "binance_futures_listed": bool(usdm_contracts or coinm_contracts),
        "binance_futures_contracts": sorted(set(usdm_contracts + coinm_contracts)),
    }
