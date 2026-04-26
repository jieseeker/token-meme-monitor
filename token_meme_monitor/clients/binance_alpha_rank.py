from __future__ import annotations

from typing import Any

import requests

from token_meme_monitor.utils import safe_float


class BinanceAlphaRankClient:
    def __init__(
        self,
        base_url: str = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list",
        timeout_seconds: int = 20,
    ) -> None:
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()
        self._headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Content-Type": "application/json",
            "Referer": "https://www.binance.com/",
        }

    def fetch_bsc_alpha_rank(self, *, page_size: int = 100) -> dict[str, dict[str, Any]]:
        page_size = max(1, min(page_size, 100))
        page = 1
        total = None
        result: dict[str, dict[str, Any]] = {}
        while total is None or len(result) < total:
            response = self._session.post(
                self._base_url,
                headers=self._headers,
                json={
                    "rankType": 20,
                    "chainId": "56",
                    "period": 50,
                    "sortBy": 0,
                    "orderAsc": False,
                    "page": page,
                    "size": page_size,
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or {}
            tokens = data.get("tokens") or []
            try:
                total = int(data.get("total")) if data.get("total") not in (None, "") else len(tokens)
            except (TypeError, ValueError):
                total = len(tokens)
            if not tokens:
                break
            for token in tokens:
                if not isinstance(token, dict):
                    continue
                address = str(token.get("contractAddress") or "").lower()
                if not address:
                    continue
                result[address] = {
                    "holder_count": _safe_int(token.get("holders")),
                    "top10_holder_share": _percent_to_ratio(token.get("holdersTop10Percent")),
                }
            if len(tokens) < page_size:
                break
            page += 1
        return result


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _percent_to_ratio(value: Any) -> float | None:
    parsed = safe_float(value)
    if parsed is None:
        return None
    return parsed / 100
