from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any

from token_meme_monitor.clients.dexscreener import DexScreenerClient


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def get(self, url: str, timeout: int) -> FakeResponse:
        return FakeResponse(self.payload)


def pair_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "chainId": "bsc",
        "pairAddress": "0xpair",
        "baseToken": {"address": "0xtoken", "symbol": "MEME", "name": "Meme"},
        "quoteToken": {"address": "0xquote", "symbol": "WBNB", "name": "Wrapped BNB"},
        "pairCreatedAt": "1777118400000",
        "dexId": "pancakeswap",
        "url": "https://dexscreener.com/bsc/0xpair",
        "priceUsd": "0.01",
        "priceNative": "0.00002",
        "liquidity": {"usd": 100000},
        "fdv": 1000000,
        "marketCap": 900000,
        "volume": {"m5": 1000, "h1": 10000, "h24": 100000},
        "txns": {"m5": {"buys": 10, "sells": 2}, "h1": {"buys": 40, "sells": 20}},
        "priceChange": {"m5": 1, "h1": 5, "h24": 20},
        "info": {"websites": [{"url": "https://example.com"}], "socials": [{"type": "x"}]},
        "boosts": {"active": 1},
    }
    payload.update(overrides)
    return {"pairs": [payload]}


class DexScreenerClientTests(unittest.TestCase):
    def test_fetch_pair_snapshot_accepts_matching_response(self) -> None:
        client = DexScreenerClient()
        client._session = FakeSession(pair_payload())
        snapshot = client.fetch_pair_snapshot(
            chain_id="bsc",
            pair_address="0xpair",
            token_address="0xtoken",
            quote_token_address="0xquote",
            quote_symbol="WBNB",
            pair_created_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.pair_address, "0xpair")
        self.assertEqual(snapshot.token_address, "0xtoken")
        self.assertEqual(snapshot.quote_token_address, "0xquote")
        self.assertEqual(snapshot.token_symbol, "MEME")

    def test_fetch_pair_snapshot_rejects_mismatched_pair_address(self) -> None:
        client = DexScreenerClient()
        client._session = FakeSession(pair_payload(pairAddress="0xotherpair"))
        snapshot = client.fetch_pair_snapshot(
            chain_id="bsc",
            pair_address="0xpair",
            token_address="0xtoken",
            quote_token_address="0xquote",
            quote_symbol="WBNB",
            pair_created_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIsNone(snapshot)

    def test_fetch_pair_snapshot_rejects_mismatched_token_side(self) -> None:
        client = DexScreenerClient()
        client._session = FakeSession(
            pair_payload(baseToken={"address": "0xother", "symbol": "OTHER", "name": "Other"})
        )
        snapshot = client.fetch_pair_snapshot(
            chain_id="bsc",
            pair_address="0xpair",
            token_address="0xtoken",
            quote_token_address="0xquote",
            quote_symbol="WBNB",
            pair_created_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIsNone(snapshot)

    def test_fetch_pair_snapshot_rejects_requested_token_on_quote_side(self) -> None:
        client = DexScreenerClient()
        client._session = FakeSession(
            pair_payload(
                baseToken={"address": "0xquote", "symbol": "WBNB", "name": "Wrapped BNB"},
                quoteToken={"address": "0xtoken", "symbol": "MEME", "name": "Meme"},
            )
        )
        snapshot = client.fetch_pair_snapshot(
            chain_id="bsc",
            pair_address="0xpair",
            token_address="0xtoken",
            quote_token_address="0xquote",
            quote_symbol="WBNB",
            pair_created_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIsNone(snapshot)


if __name__ == "__main__":
    unittest.main()
