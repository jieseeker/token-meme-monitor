from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from token_meme_monitor.clients.geckoterminal import compute_lookback_returns


class GeckoTerminalTests(unittest.TestCase):
    def test_compute_lookback_returns_uses_latest_rows_before_cutoff(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 45, tzinfo=timezone.utc)
        rows = [
            {"ts": int((observed_at - timedelta(hours=24, minutes=10)).timestamp()), "close": 1.0},
            {"ts": int((observed_at - timedelta(hours=23, minutes=50)).timestamp()), "close": 10.0},
            {"ts": int((observed_at - timedelta(hours=2, minutes=5)).timestamp()), "close": 1.5},
            {"ts": int((observed_at - timedelta(hours=1, minutes=55)).timestamp()), "close": 9.0},
            {"ts": int((observed_at - timedelta(minutes=5)).timestamp()), "close": 3.0},
        ]
        result = compute_lookback_returns(rows, observed_at=observed_at)
        self.assertAlmostEqual(result["external_return_2h"] or 0.0, 1.0)
        self.assertAlmostEqual(result["external_return_24h"] or 0.0, 2.0)

    def test_compute_lookback_returns_returns_none_when_history_is_missing(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 45, tzinfo=timezone.utc)
        rows = [{"ts": int((observed_at - timedelta(minutes=5)).timestamp()), "close": 3.0}]
        result = compute_lookback_returns(rows, observed_at=observed_at)
        self.assertIsNone(result["external_return_2h"])
        self.assertIsNone(result["external_return_24h"])


if __name__ == "__main__":
    unittest.main()
