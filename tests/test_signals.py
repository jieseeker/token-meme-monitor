from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from token_meme_monitor.config import SignalConfig
from token_meme_monitor.features import build_feature_vector
from token_meme_monitor.models import PairSnapshot
from token_meme_monitor.signals import SignalEngine


class SignalEngineTests(unittest.TestCase):
    def test_high_quality_snapshot_becomes_alert_candidate(self) -> None:
        now = datetime.now(timezone.utc)
        snapshot = PairSnapshot(
            pair_address="0xpair",
            token_address="0xtoken",
            token_symbol="MEME",
            token_name="Meme",
            quote_token_address="0xquote",
            quote_symbol="WBNB",
            observed_at=now,
            pair_created_at=now - timedelta(minutes=20),
            dex_id="pancakeswap",
            pair_url="https://dexscreener.com/bsc/0xpair",
            price_usd=0.0012,
            price_native=0.0000018,
            liquidity_usd=85_000,
            fdv=600_000,
            market_cap=420_000,
            volume_m5=6_000,
            volume_h1=48_000,
            volume_h24=51_000,
            buys_m5=24,
            sells_m5=7,
            buys_h1=120,
            sells_h1=62,
            price_change_m5=12,
            price_change_h1=38,
            price_change_h24=38,
            website_count=1,
            social_count=2,
            boosts_active=1,
            raw_payload={},
        )
        config = SignalConfig()
        features = build_feature_vector(snapshot, config)
        decision = SignalEngine(config).evaluate(features, observed_at=now)
        self.assertTrue(decision.should_alert)
        self.assertEqual(decision.pair_state, "alerted")
        self.assertGreaterEqual(decision.score, config.alert_score_threshold)

    def test_low_liquidity_snapshot_is_penalized(self) -> None:
        now = datetime.now(timezone.utc)
        snapshot = PairSnapshot(
            pair_address="0xpair",
            token_address="0xtoken",
            token_symbol="MEME",
            token_name="Meme",
            quote_token_address="0xquote",
            quote_symbol="WBNB",
            observed_at=now,
            pair_created_at=now - timedelta(minutes=15),
            dex_id="pancakeswap",
            pair_url="",
            price_usd=0.0002,
            price_native=0.0000002,
            liquidity_usd=900,
            fdv=90_000,
            market_cap=90_000,
            volume_m5=20,
            volume_h1=100,
            volume_h24=200,
            buys_m5=1,
            sells_m5=5,
            buys_h1=4,
            sells_h1=12,
            price_change_m5=-5,
            price_change_h1=-30,
            price_change_h24=-30,
            website_count=0,
            social_count=0,
            boosts_active=0,
            raw_payload={},
        )
        config = SignalConfig()
        features = build_feature_vector(snapshot, config)
        decision = SignalEngine(config).evaluate(features, observed_at=now)
        self.assertIn("low_liquidity", decision.risk_flags)
        self.assertEqual(decision.pair_state, "archived")
        self.assertFalse(decision.should_alert)

    def test_severe_risk_never_alerts_even_when_score_crosses_threshold(self) -> None:
        now = datetime.now(timezone.utc)
        snapshot = PairSnapshot(
            pair_address="0xpair",
            token_address="0xtoken",
            token_symbol="MEME",
            token_name="Meme",
            quote_token_address="0xquote",
            quote_symbol="WBNB",
            observed_at=now,
            pair_created_at=now - timedelta(minutes=15),
            dex_id="pancakeswap",
            pair_url="",
            price_usd=0.0002,
            price_native=0.0000002,
            liquidity_usd=1_000,
            fdv=10_000,
            market_cap=10_000,
            volume_m5=20_000,
            volume_h1=100_000,
            volume_h24=200_000,
            buys_m5=50,
            sells_m5=1,
            buys_h1=200,
            sells_h1=10,
            price_change_m5=10,
            price_change_h1=50,
            price_change_h24=50,
            website_count=1,
            social_count=1,
            boosts_active=1,
            raw_payload={},
        )
        config = SignalConfig(alert_score_threshold=50, focus_score_threshold=40)
        features = build_feature_vector(snapshot, config)
        decision = SignalEngine(config).evaluate(features, observed_at=now)
        self.assertIn("liquidity_near_zero", decision.risk_flags)
        self.assertGreaterEqual(decision.score, config.alert_score_threshold)
        self.assertEqual(decision.pair_state, "archived")
        self.assertFalse(decision.should_alert)

    def test_binance_alpha_hotspot_with_structural_risk_becomes_focused_not_alerted(self) -> None:
        now = datetime.now(timezone.utc)
        snapshot = PairSnapshot(
            pair_address="0xpair",
            token_address="0xtoken",
            token_symbol="BSB",
            token_name="Block Street",
            quote_token_address="0xquote",
            quote_symbol="USDT",
            observed_at=now,
            pair_created_at=now - timedelta(days=50),
            dex_id="uniswap",
            pair_url="",
            price_usd=0.8821,
            price_native=0.001,
            liquidity_usd=23_153.22,
            fdv=872_037_637.36,
            market_cap=181_165_819.16,
            volume_m5=43_246.22,
            volume_h1=311_429.46,
            volume_h24=2_989_763.89,
            buys_m5=134,
            sells_m5=63,
            buys_h1=1308,
            sells_h1=1275,
            price_change_m5=-1.82,
            price_change_h1=36.75,
            price_change_h24=68.58,
            website_count=0,
            social_count=0,
            boosts_active=0,
            raw_payload={},
        )
        config = SignalConfig()
        features = build_feature_vector(snapshot, config, monitor_universe="binance_alpha")
        decision = SignalEngine(config).evaluate(
            features,
            observed_at=now,
            monitor_universe="binance_alpha",
            token_metadata={
                "alpha_score": 111,
                "holder_count": 54862,
                "binance_futures_listed": True,
            },
        )

        self.assertGreaterEqual(decision.score, config.focus_score_threshold)
        self.assertEqual(decision.pair_state, "focused")
        self.assertFalse(decision.should_alert)
        self.assertIn("alpha_hot_score", decision.reasons)
        self.assertIn("binance_futures_listed", decision.reasons)
        self.assertIn("fdv_liquidity_stretched", decision.risk_flags)


if __name__ == "__main__":
    unittest.main()
