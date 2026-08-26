from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.strategy_feedback import (
    build_strategy_feedback_report,
    render_strategy_feedback_markdown,
)
from token_meme_monitor.utils import json_dumps


class StrategyFeedbackTests(unittest.TestCase):
    def test_feedback_report_builds_slice_metrics_and_review_only_recommendations(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        rows = [
            _row(observed_at, "0xpair-a", "0xtoken-a", stage="early", score=76, hit_2h=1),
            _row(observed_at + timedelta(hours=3), "0xpair-b", "0xtoken-b", stage="early", score=78, hit_2h=1),
            _row(observed_at + timedelta(hours=6), "0xpair-c", "0xtoken-c", stage="exhaustion", score=76, hit_2h=0),
            _row(observed_at + timedelta(hours=9), "0xpair-d", "0xtoken-d", stage="exhaustion", score=78, hit_2h=0),
        ]

        report = build_strategy_feedback_report(rows, min_slice_events=2, generated_at=observed_at)

        self.assertEqual(report["summary"]["prediction_count"], 4)
        self.assertEqual(report["summary"]["outcome_count"], 4)
        stage_slices = {
            item["slice_key"]: item
            for item in report["slices"]
            if item["dimension"] == "stage"
        }
        self.assertEqual(stage_slices["early"]["metrics"]["win_rate_2h"], 1.0)
        self.assertEqual(stage_slices["exhaustion"]["metrics"]["win_rate_2h"], 0.0)
        self.assertTrue(any(item["suggested_action"] == "review_for_more_weight" for item in report["recommendations"]))
        self.assertTrue(any(item["suggested_action"] == "investigate_or_downweight" for item in report["recommendations"]))

    def test_feedback_report_is_persisted_and_loaded_as_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            generated_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            report = build_strategy_feedback_report(
                [
                    _row(generated_at, "0xpair-a", "0xtoken-a", stage="early", score=76, hit_2h=1),
                    _row(generated_at + timedelta(hours=3), "0xpair-b", "0xtoken-b", stage="early", score=78, hit_2h=1),
                ],
                min_slice_events=2,
                generated_at=generated_at,
            )

            run_id = repo.insert_strategy_feedback_report(report)
            latest = repo.get_latest_strategy_feedback_report()
            repo.close()

            self.assertGreater(run_id, 0)
            self.assertIsNotNone(latest)
            self.assertEqual(latest["run_id"], run_id)
            self.assertEqual(latest["summary"]["prediction_count"], 2)

    def test_markdown_includes_summary_and_recommendations(self) -> None:
        generated_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        report = build_strategy_feedback_report(
            [
                _row(generated_at, "0xpair-a", "0xtoken-a", stage="early", score=76, hit_2h=1),
                _row(generated_at + timedelta(hours=3), "0xpair-b", "0xtoken-b", stage="early", score=78, hit_2h=1),
            ],
            min_slice_events=2,
            generated_at=generated_at,
        )

        markdown = render_strategy_feedback_markdown(report)

        self.assertIn("# Strategy Feedback Report", markdown)
        self.assertIn("## Recommendations", markdown)


def _row(
    observed_at: datetime,
    pair_address: str,
    token_address: str,
    *,
    stage: str,
    score: int,
    hit_2h: int,
) -> dict:
    return {
        "signal_id": f"{pair_address}-{int(observed_at.timestamp())}",
        "pair_address": pair_address,
        "token_address": token_address,
        "token_symbol": token_address[-1].upper(),
        "observed_at": observed_at.isoformat(timespec="seconds"),
        "score": score,
        "strategy_version": "v1",
        "pair_state": "focused",
        "should_alert": 0,
        "reasons": json_dumps(["h1_volume_support"]),
        "risk_flags": json_dumps([]),
        "feature_json": json_dumps(
            {
                "age_minutes": 120,
                "market_cap": 750_000,
                "market_cap_bucket": "micro",
                "liquidity_usd": 80_000,
                "volume_to_liquidity_h1": 1.2,
            }
        ),
        "token_metadata_json": json_dumps({"alpha_score": 111}),
        "stage": stage,
        "short_momentum_score": score,
        "opportunity_score": score,
        "prob_2h_up20": 0.50,
        "prob_6h_up50": 0.20,
        "prob_24h_up100": 0.10,
        "risk_6h_dd30": 0.15,
        "sample_count_2h": 2,
        "sample_count_6h": 6,
        "sample_count_24h": 24,
        "hit_2h_up20": hit_2h,
        "hit_6h_up50": 0,
        "hit_24h_up100": 0,
        "hit_6h_dd30": 0,
        "price_divergence_pct": 0.01,
        "quality_flags_json": json_dumps([]),
    }


if __name__ == "__main__":
    unittest.main()
