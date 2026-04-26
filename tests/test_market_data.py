from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from token_meme_monitor.market_data import sanitize_alpha_metadata, sanitize_pair_snapshot
from token_meme_monitor.models import PairSnapshot
from token_meme_monitor.utils import safe_float


class MarketDataTests(unittest.TestCase):
    def test_safe_float_rejects_non_finite_values(self) -> None:
        self.assertIsNone(safe_float("NaN"))
        self.assertIsNone(safe_float("inf"))
        self.assertIsNone(safe_float(float("nan")))

    def test_alpha_metadata_and_snapshot_are_sanitized(self) -> None:
        now = datetime.now(timezone.utc)
        snapshot = PairSnapshot(
            pair_address="0xpair",
            token_address="0xtoken",
            token_symbol="SPY",
            token_name="Spy",
            quote_token_address="0xquote",
            quote_symbol="WBNB",
            observed_at=now,
            pair_created_at=now - timedelta(days=1),
            dex_id="pancakeswap",
            pair_url="https://dexscreener.com/bsc/0xpair",
            price_usd=3.402567865406515e38,
            price_native=0.5,
            liquidity_usd=250000.0,
            fdv=None,
            market_cap=None,
            volume_m5=1000.0,
            volume_h1=15000.0,
            volume_h24=200000.0,
            buys_m5=10,
            sells_m5=2,
            buys_h1=100,
            sells_h1=60,
            price_change_m5=4.0,
            price_change_h1=12.0,
            price_change_h24=20.0,
            website_count=1,
            social_count=1,
            boosts_active=0,
            raw_payload={},
        )
        alpha_metadata = sanitize_alpha_metadata(
            {
                "alpha_price": 714.6419273407671,
                "alpha_market_cap": 39044684.45585241,
                "alpha_fdv": 39044684.45585241,
                "alpha_liquidity": 1800000.0,
                "alpha_volume_24h": 750000.0,
                "holder_count": "12345",
            }
        )
        cleaned = sanitize_pair_snapshot(
            snapshot,
            monitor_universe="binance_alpha",
            alpha_reference={
                "price": alpha_metadata["alpha_price"],
                "market_cap": alpha_metadata["alpha_market_cap"],
                "fdv": alpha_metadata["alpha_fdv"],
                "liquidity": alpha_metadata["alpha_liquidity"],
                "volume_24h": alpha_metadata["alpha_volume_24h"],
            },
        )
        self.assertAlmostEqual(cleaned.price_usd or 0.0, alpha_metadata["alpha_price"] or 0.0)
        self.assertAlmostEqual(cleaned.market_cap or 0.0, alpha_metadata["alpha_market_cap"] or 0.0)
        self.assertAlmostEqual(cleaned.fdv or 0.0, alpha_metadata["alpha_fdv"] or 0.0)
        self.assertIn("price_usd_invalid", cleaned.raw_payload["_data_quality"]["flags"])
        self.assertEqual(cleaned.raw_payload["_data_quality"]["sources"]["price_usd"], "alpha_reference")

    def test_binance_alpha_mode_preserves_valid_dexscreener_price(self) -> None:
        now = datetime.now(timezone.utc)
        snapshot = PairSnapshot(
            pair_address="0xpair",
            token_address="0xtoken",
            token_symbol="LIVE",
            token_name="Live",
            quote_token_address="0xquote",
            quote_symbol="WBNB",
            observed_at=now,
            pair_created_at=now - timedelta(hours=1),
            dex_id="pancakeswap",
            pair_url="https://dexscreener.com/bsc/0xpair",
            price_usd=0.012,
            price_native=0.00002,
            liquidity_usd=250000.0,
            fdv=900000.0,
            market_cap=850000.0,
            volume_m5=1000.0,
            volume_h1=15000.0,
            volume_h24=200000.0,
            buys_m5=10,
            sells_m5=2,
            buys_h1=100,
            sells_h1=60,
            price_change_m5=4.0,
            price_change_h1=12.0,
            price_change_h24=20.0,
            website_count=1,
            social_count=1,
            boosts_active=0,
            raw_payload={},
        )

        cleaned = sanitize_pair_snapshot(
            snapshot,
            monitor_universe="binance_alpha",
            alpha_reference={
                "price": 0.006,
                "market_cap": 39044684.45585241,
                "fdv": 39044684.45585241,
                "liquidity": 1800000.0,
                "volume_24h": 750000.0,
            },
        )

        self.assertEqual(cleaned.price_usd, 0.012)
        self.assertEqual(cleaned.raw_payload["_data_quality"]["sources"]["price_usd"], "dexscreener")


if __name__ == "__main__":
    unittest.main()
