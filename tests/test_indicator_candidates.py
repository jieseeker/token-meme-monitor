from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from token_meme_monitor.indicator_candidates import compute_candidate_indicators, market_cap_bucket


class IndicatorCandidateTests(unittest.TestCase):
    def test_market_cap_bucket(self) -> None:
        self.assertEqual(market_cap_bucket(500_000), "<1M")
        self.assertEqual(market_cap_bucket(5_000_000), "1M-10M")
        self.assertEqual(market_cap_bucket(20_000_000), "10M-50M")
        self.assertEqual(market_cap_bucket(80_000_000), "50M+")

    def test_candidate_indicators_from_snapshot_history(self) -> None:
        now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        history = []
        for hour in range(1, 73):
            history.append(
                {
                    "observed_at": now - timedelta(hours=hour),
                    "price_usd": 1.0 + hour * 0.01,
                    "volume_h1": 1000.0,
                    "market_cap": 2_000_000.0,
                    "fdv": 5_000_000.0,
                }
            )
        indicators = compute_candidate_indicators(
            observed_at=now,
            price_usd=2.0,
            volume_h1=5000.0,
            market_cap=2_000_000.0,
            fdv=5_000_000.0,
            history_rows=history,
        )
        self.assertEqual(indicators["market_cap_bucket"], "1M-10M")
        self.assertAlmostEqual(indicators["volume_impulse_vs_prev24h"] or 0.0, 5.0)
        self.assertAlmostEqual(indicators["volume_impulse_vs_prev72h"] or 0.0, 5.0)
        self.assertIsNotNone(indicators["h1_return_live"])
        self.assertIsNotNone(indicators["h4_return_live"])
        self.assertIsNotNone(indicators["h24_return_live"])

    def test_live_returns_use_raw_snapshot_history_for_non_hour_boundary(self) -> None:
        now = datetime(2026, 4, 25, 12, 45, tzinfo=timezone.utc)
        history = [
            {
                "observed_at": (now - timedelta(hours=4, minutes=5)).isoformat(),
                "price_usd": 0.75,
                "volume_h1": 1000.0,
                "market_cap": 2_000_000.0,
                "fdv": 5_000_000.0,
            },
            {
                "observed_at": (now - timedelta(hours=3, minutes=55)).isoformat(),
                "price_usd": 10.0,
                "volume_h1": 1000.0,
                "market_cap": 2_000_000.0,
                "fdv": 5_000_000.0,
            },
            {
                "observed_at": (now - timedelta(hours=2, minutes=55)).isoformat(),
                "price_usd": 1.0,
                "volume_h1": 1000.0,
                "market_cap": 2_000_000.0,
                "fdv": 5_000_000.0,
            },
            {
                "observed_at": (now - timedelta(hours=1, minutes=55)).isoformat(),
                "price_usd": 1.5,
                "volume_h1": 1000.0,
                "market_cap": 2_000_000.0,
                "fdv": 5_000_000.0,
            },
            {
                "observed_at": (now - timedelta(hours=1, minutes=5)).isoformat(),
                "price_usd": 2.0,
                "volume_h1": 1000.0,
                "market_cap": 2_000_000.0,
                "fdv": 5_000_000.0,
            },
        ]
        indicators = compute_candidate_indicators(
            observed_at=now,
            price_usd=3.0,
            volume_h1=5000.0,
            market_cap=2_000_000.0,
            fdv=5_000_000.0,
            history_rows=history,
        )
        self.assertAlmostEqual(indicators["h1_return_live"] or 0.0, 0.5)
        self.assertAlmostEqual(indicators["h4_return_live"] or 0.0, 3.0)


if __name__ == "__main__":
    unittest.main()
