from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from token_meme_monitor.prediction_backtest import build_prediction_backtest_report
from token_meme_monitor.utils import json_dumps


class PredictionBacktestTests(unittest.TestCase):
    def test_report_filters_price_divergence_and_dedupes_nearby_pair_events(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        rows = [
            _row(observed_at, "0xpair-a", "0xtoken-a", price_divergence_pct=0.01, hit_2h=0),
            _row(observed_at + timedelta(hours=3), "0xpair-b", "0xtoken-b", price_divergence_pct=0.01, hit_2h=1),
            _row(observed_at + timedelta(hours=6), "0xpair-c", "0xtoken-c", price_divergence_pct=0.01, hit_2h=1),
            _row(observed_at + timedelta(hours=6, minutes=30), "0xpair-c", "0xtoken-c", price_divergence_pct=0.01, hit_2h=0),
            _row(observed_at + timedelta(hours=9), "0xpair-d", "0xtoken-d", price_divergence_pct=0.25, hit_2h=1),
        ]

        report = build_prediction_backtest_report(rows, train_ratio=0.5, max_price_divergence_pct=0.10)

        self.assertEqual(report["total_rows"], 5)
        self.assertEqual(report["quality"]["rows_excluded_by_price_divergence"], 1)
        self.assertEqual(report["duplicate_events_skipped"], 1)
        self.assertEqual(report["usable_events"], 3)
        self.assertEqual(report["train_events"], 1)
        self.assertEqual(report["test_events"], 2)
        self.assertEqual(sum(bucket["events"] for bucket in report["buckets"].values()), 2)

    def test_report_marks_horizon_rates_from_eligible_samples_only(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        rows = [
            _row(observed_at, "0xpair-a", "0xtoken-a", sample_count_2h=2, sample_count_6h=6, sample_count_24h=24),
            _row(
                observed_at + timedelta(hours=3),
                "0xpair-b",
                "0xtoken-b",
                sample_count_2h=1,
                sample_count_6h=6,
                sample_count_24h=24,
                hit_2h=1,
            ),
            _row(
                observed_at + timedelta(hours=6),
                "0xpair-c",
                "0xtoken-c",
                sample_count_2h=2,
                sample_count_6h=4,
                sample_count_24h=10,
                hit_2h=1,
                hit_6h=1,
                hit_24h=1,
            ),
        ]

        report = build_prediction_backtest_report(rows, train_ratio=0.34)
        aggregate = report["aggregate"]

        self.assertEqual(aggregate["sample_2h_up20"], 1)
        self.assertEqual(aggregate["hit_2h_up20"], 1)
        self.assertEqual(aggregate["sample_6h_up50"], 1)
        self.assertEqual(aggregate["sample_24h_up100"], 1)


def _row(
    observed_at: datetime,
    pair_address: str,
    token_address: str,
    *,
    price_divergence_pct: float = 0.0,
    sample_count_2h: int = 2,
    sample_count_6h: int = 6,
    sample_count_24h: int = 24,
    hit_2h: int = 0,
    hit_6h: int = 0,
    hit_24h: int = 0,
) -> dict:
    return {
        "signal_id": f"{pair_address}-{int(observed_at.timestamp())}",
        "pair_address": pair_address,
        "token_address": token_address,
        "observed_at": observed_at.isoformat(timespec="seconds"),
        "score": 65,
        "strategy_version": "v1",
        "pair_state": "focused",
        "should_alert": 0,
        "reasons": json_dumps(["h1_volume_support"]),
        "risk_flags": json_dumps([]),
        "feature_json": json_dumps(
            {
                "volume_to_liquidity_h1": 1.2,
                "h1_return_live": 0.18,
                "h24_return_live": 0.4,
                "liquidity_to_fdv": 0.02,
            }
        ),
        "token_metadata_json": json_dumps({"alpha_score": 111}),
        "stage": "acceleration",
        "sample_count_2h": sample_count_2h,
        "sample_count_6h": sample_count_6h,
        "sample_count_24h": sample_count_24h,
        "hit_2h_up20": hit_2h,
        "hit_6h_up50": hit_6h,
        "hit_24h_up100": hit_24h,
        "hit_6h_dd30": 0,
        "price_divergence_pct": price_divergence_pct,
        "quality_flags_json": json_dumps([]),
    }


if __name__ == "__main__":
    unittest.main()
