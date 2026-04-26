from __future__ import annotations

from datetime import datetime, timezone

import requests

from token_meme_monitor.models import AlphaToken
from token_meme_monitor.utils import safe_float


class BinanceAlphaClient:
    def __init__(self, base_url: str, timeout_seconds: int = 20) -> None:
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()
        self._headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.binance.com/",
        }

    def fetch_bsc_tokens(self) -> list[AlphaToken]:
        response = self._session.get(
            self._base_url,
            headers=self._headers,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("data") or []
        tokens: list[AlphaToken] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("chainId")) != "56":
                continue
            if bool(item.get("fullyDelisted")) or bool(item.get("offline")):
                continue
            address = str(item.get("contractAddress") or "").lower()
            if not address:
                continue
            listing_time = _parse_millis(item.get("listingTime"))
            holders = item.get("holders")
            try:
                holder_count = int(holders) if holders not in (None, "") else None
            except (TypeError, ValueError):
                holder_count = None
            try:
                alpha_score = int(item.get("score")) if item.get("score") not in (None, "") else None
            except (TypeError, ValueError):
                alpha_score = None
            tokens.append(
                AlphaToken(
                    token_address=address,
                    chain_id=str(item.get("chainId") or ""),
                    chain_name=str(item.get("chainName") or ""),
                    symbol=str(item.get("symbol") or address[:8]),
                    name=str(item.get("name") or address[:8]),
                    price=safe_float(item.get("price")),
                    market_cap=safe_float(item.get("marketCap")),
                    fdv=safe_float(item.get("fdv")),
                    liquidity=safe_float(item.get("liquidity")),
                    volume_24h=safe_float(item.get("volume24h")),
                    holders=holder_count,
                    alpha_id=str(item.get("alphaId") or "") or None,
                    alpha_score=alpha_score,
                    listing_time=listing_time,
                    raw_payload=item,
                )
            )
        return tokens


def _parse_millis(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None

