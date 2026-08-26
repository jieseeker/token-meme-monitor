from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from token_meme_monitor.models import PairSnapshot
from token_meme_monitor.utils import safe_float, safe_int

LOGGER = logging.getLogger(__name__)


class DexScreenerClient:
    def __init__(self, base_url: str = "https://api.dexscreener.com", timeout_seconds: int = 10) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()

    def fetch_pair_snapshot(
        self,
        *,
        chain_id: str,
        pair_address: str,
        token_address: str,
        quote_token_address: str,
        quote_symbol: str,
        pair_created_at: datetime,
    ) -> PairSnapshot | None:
        url = f"{self._base_url}/latest/dex/pairs/{chain_id}/{pair_address}"
        response = self._session.get(url, timeout=self._timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        pairs = payload.get("pairs") or []
        if not pairs:
            return None
        pair = pairs[0]
        if not isinstance(pair, dict):
            return None
        if not _matches_requested_pair(
            pair,
            chain_id=chain_id,
            pair_address=pair_address,
            token_address=token_address,
            quote_token_address=quote_token_address,
        ):
            LOGGER.warning("DexScreener pair response did not match requested pair %s", pair_address)
            return None
        observed_at = datetime.now(timezone.utc)
        pair_created_ms = pair.get("pairCreatedAt")
        if pair_created_ms:
            try:
                pair_created_at = datetime.fromtimestamp(int(pair_created_ms) / 1000, tz=timezone.utc)
            except (TypeError, ValueError):
                LOGGER.debug("invalid pairCreatedAt for %s", pair_address)
        info = pair.get("info") or {}
        socials = info.get("socials") or []
        websites = info.get("websites") or []
        boosts = pair.get("boosts") or {}
        return PairSnapshot(
            pair_address=pair_address,
            token_address=token_address,
            token_symbol=((pair.get("baseToken") or {}).get("symbol") or "").strip(),
            token_name=((pair.get("baseToken") or {}).get("name") or "").strip(),
            quote_token_address=quote_token_address,
            quote_symbol=quote_symbol,
            observed_at=observed_at,
            pair_created_at=pair_created_at,
            dex_id=(pair.get("dexId") or "").strip(),
            pair_url=(pair.get("url") or "").strip(),
            price_usd=safe_float(pair.get("priceUsd")),
            price_native=safe_float(pair.get("priceNative")),
            liquidity_usd=safe_float((pair.get("liquidity") or {}).get("usd")),
            fdv=safe_float(pair.get("fdv")),
            market_cap=safe_float(pair.get("marketCap")),
            volume_m5=safe_float((pair.get("volume") or {}).get("m5")) or 0.0,
            volume_h1=safe_float((pair.get("volume") or {}).get("h1")) or 0.0,
            volume_h24=safe_float((pair.get("volume") or {}).get("h24")) or 0.0,
            buys_m5=safe_int((((pair.get("txns") or {}).get("m5") or {}).get("buys"))),
            sells_m5=safe_int((((pair.get("txns") or {}).get("m5") or {}).get("sells"))),
            buys_h1=safe_int((((pair.get("txns") or {}).get("h1") or {}).get("buys"))),
            sells_h1=safe_int((((pair.get("txns") or {}).get("h1") or {}).get("sells"))),
            price_change_m5=safe_float((pair.get("priceChange") or {}).get("m5")) or 0.0,
            price_change_h1=safe_float((pair.get("priceChange") or {}).get("h1")) or 0.0,
            price_change_h24=safe_float((pair.get("priceChange") or {}).get("h24")) or 0.0,
            website_count=len(websites),
            social_count=len(socials),
            boosts_active=safe_int(boosts.get("active")),
            raw_payload=pair if isinstance(pair, dict) else {},
        )

    def fetch_best_pairs_for_tokens(
        self,
        *,
        chain_id: str,
        token_addresses: list[str],
        quote_tokens: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        clean_addresses = [address.lower() for address in token_addresses if address]
        if not clean_addresses:
            return {}
        path = ",".join(clean_addresses)
        url = f"{self._base_url}/tokens/v1/{chain_id}/{path}"
        response = self._session.get(url, timeout=self._timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return {}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for pair in payload:
            if not isinstance(pair, dict):
                continue
            base_token = (pair.get("baseToken") or {}).get("address")
            quote_token = (pair.get("quoteToken") or {}).get("address")
            if not base_token or not quote_token:
                continue
            base_token = str(base_token).lower()
            quote_token = str(quote_token).lower()
            if quote_token not in quote_tokens:
                continue
            grouped.setdefault(base_token, []).append(pair)
        result: dict[str, dict[str, Any]] = {}
        for token_address, pairs in grouped.items():
            best = max(
                pairs,
                key=lambda pair: (
                    safe_float((pair.get("liquidity") or {}).get("usd")) or 0.0,
                    safe_float((pair.get("volume") or {}).get("h24")) or 0.0,
                ),
            )
            quote_token_address = str(((best.get("quoteToken") or {}).get("address")) or "").lower()
            result[token_address] = {
                "pair_address": str(best.get("pairAddress") or "").lower(),
                "token_address": token_address,
                "token_symbol": ((best.get("baseToken") or {}).get("symbol") or "").strip(),
                "token_name": ((best.get("baseToken") or {}).get("name") or "").strip(),
                "quote_token_address": quote_token_address,
                "quote_symbol": quote_tokens.get(quote_token_address, ""),
                "token0_address": token_address,
                "token1_address": quote_token_address,
                "pair_created_at": _parse_pair_created_at(best.get("pairCreatedAt")),
                "dex_id": (best.get("dexId") or "").strip(),
                "pair_url": (best.get("url") or "").strip(),
                "raw_payload": best,
            }
        return result


def _matches_requested_pair(
    pair: dict[str, Any],
    *,
    chain_id: str,
    pair_address: str,
    token_address: str,
    quote_token_address: str,
) -> bool:
    response_chain_id = str(pair.get("chainId") or "").lower()
    if response_chain_id and response_chain_id != chain_id.lower():
        return False

    response_pair_address = str(pair.get("pairAddress") or "").lower()
    if response_pair_address and response_pair_address != pair_address.lower():
        return False

    base_token = str(((pair.get("baseToken") or {}).get("address")) or "").lower()
    quote_token = str(((pair.get("quoteToken") or {}).get("address")) or "").lower()
    requested_token = token_address.lower()
    requested_quote = quote_token_address.lower()
    if requested_token == requested_quote or not base_token or not quote_token:
        return False
    return base_token == requested_token and quote_token == requested_quote


def _parse_pair_created_at(value) -> datetime:
    if value in (None, ""):
        return datetime.now(timezone.utc)
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
