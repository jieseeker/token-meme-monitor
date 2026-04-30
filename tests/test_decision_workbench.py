from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.decision_workbench import (
    build_decision_case,
    build_decision_queues,
    export_cases_csv,
)
from token_meme_monitor.utils import json_dumps


class DecisionWorkbenchTests(unittest.TestCase):
    def test_decision_case_connects_signal_prediction_outcome_and_note(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        case = build_decision_case(
            _row(observed_at, signal_id=123, short_score=78, hit_2h=1, max_return_2h=0.35),
            note={"note": "reviewed", "watchlisted": True},
        )

        self.assertEqual(case["case_id"], "signal:123")
        self.assertEqual(case["prediction"]["short_momentum_score"], 78)
        self.assertEqual(case["outcome"]["hit_2h_up20"], 1)
        self.assertEqual(case["note"]["note"], "reviewed")
        self.assertEqual(case["timeline"][0]["event"], "signal_observed")

    def test_decision_queues_cover_high_confidence_misses_wins_and_stale_backlog(self) -> None:
        now = datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)
        rows = [
            _row(now - timedelta(hours=1), signal_id=1, short_score=82, hit_2h=1, max_return_2h=0.40),
            _row(now - timedelta(hours=2), signal_id=2, short_score=80, hit_2h=0, max_return_2h=0.0),
            _row(now - timedelta(hours=26), signal_id=3, short_score=55, sample_count_2h=0, max_return_2h=None),
        ]

        queues = build_decision_queues(rows, now=now)

        self.assertEqual([case["case_id"] for case in queues["high_confidence"]], ["signal:1", "signal:2"])
        self.assertEqual([case["case_id"] for case in queues["missed_prediction"]], ["signal:2"])
        self.assertEqual([case["case_id"] for case in queues["strong_win"]], ["signal:1"])
        self.assertEqual([case["case_id"] for case in queues["stale_data"]], ["signal:3"])

    def test_decision_note_persistence_is_isolated_from_scoring_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()

            repo.upsert_decision_note("signal:123", note="watch this", watchlisted=True)
            note = repo.get_decision_note("signal:123")
            signal_count = repo._conn.execute("SELECT count(*) FROM signals").fetchone()[0]
            repo.close()

            self.assertEqual(note["note"], "watch this")
            self.assertTrue(note["watchlisted"])
            self.assertEqual(signal_count, 0)

    def test_export_cases_csv_matches_filtered_cases(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        cases = [
            build_decision_case(_row(observed_at, signal_id=123, short_score=78, hit_2h=1, max_return_2h=0.35))
        ]

        csv_text = export_cases_csv(cases)

        self.assertIn("case_id", csv_text.splitlines()[0])
        self.assertIn("signal:123", csv_text)


def _row(
    observed_at: datetime,
    *,
    signal_id: int,
    short_score: int,
    hit_2h: int = 0,
    sample_count_2h: int = 2,
    max_return_2h: float | None = 0.0,
) -> dict:
    return {
        "signal_id": signal_id,
        "pair_address": f"0xpair{signal_id}",
        "token_address": f"0xtoken{signal_id}",
        "token_symbol": f"T{signal_id}",
        "token_name": f"Token {signal_id}",
        "observed_at": observed_at.isoformat(timespec="seconds"),
        "score": 70,
        "pair_state": "focused",
        "reasons": json_dumps(["h1_volume_support"]),
        "risk_flags": json_dumps([]),
        "feature_json": json_dumps({"price_usd": 1.0, "liquidity_usd": 80_000}),
        "prediction_reasons": json_dumps(["prediction_price_accelerating"]),
        "prob_2h_up20": 0.25,
        "prob_6h_up50": 0.10,
        "prob_24h_up100": 0.05,
        "risk_6h_dd30": 0.08,
        "short_momentum_score": short_score,
        "opportunity_score": short_score,
        "continuation_score": 40,
        "breakout_score": 20,
        "stage": "acceleration",
        "hit_2h_up20": hit_2h,
        "hit_6h_up50": 0,
        "hit_24h_up100": 0,
        "sample_count_2h": sample_count_2h,
        "sample_count_6h": sample_count_2h,
        "sample_count_24h": sample_count_2h,
        "max_return_2h": max_return_2h,
        "max_return_6h": max_return_2h,
        "max_return_24h": max_return_2h,
        "min_return_6h": -0.02 if sample_count_2h else None,
    }


if __name__ == "__main__":
    unittest.main()
