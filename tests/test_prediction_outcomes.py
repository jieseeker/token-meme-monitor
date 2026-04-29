from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.models import PredictionResult, SignalDecision
from token_meme_monitor.prediction_outcomes import (
    OUTCOME_BEFORE_MARGIN_HOURS,
    OUTCOME_OHLCV_LIMIT,
    compute_prediction_outcome_from_ohlcv,
    compute_prediction_outcome_with_hourly_ohlcv,
)
from token_meme_monitor.utils import json_dumps


class _StubGeckoClient:
    def __init__(self, rows: list[dict[str, float]]) -> None:
        self.rows = rows
        self.calls = 0

    def fetch_pool_ohlcv(
        self,
        pool_address: str,
        *,
        timeframe: str,
        aggregate: int,
        limit: int,
        before_timestamp: int | None = None,
    ) -> list[dict[str, float]]:
        self.calls += 1
        self.last_request = {
            "pool_address": pool_address,
            "timeframe": timeframe,
            "aggregate": aggregate,
            "limit": limit,
            "before_timestamp": before_timestamp,
        }
        return list(self.rows)


class PredictionOutcomeTests(unittest.TestCase):
    def test_hourly_outcome_uses_signal_price_and_high_low_windows(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 30, tzinfo=timezone.utc)
        rows = [
            _ohlcv_row(observed_at - timedelta(minutes=30), high=1.05, low=0.95, close=1.0),
            _ohlcv_row(observed_at + timedelta(minutes=30), high=1.25, low=0.85, close=1.1),
            _ohlcv_row(observed_at + timedelta(hours=1, minutes=30), high=1.4, low=0.9, close=1.2),
            _ohlcv_row(observed_at + timedelta(hours=5, minutes=30), high=1.6, low=0.7, close=1.3),
            _ohlcv_row(observed_at + timedelta(hours=23, minutes=30), high=2.2, low=1.1, close=2.0),
        ]

        outcome = compute_prediction_outcome_from_ohlcv(rows, observed_at=observed_at, base_price=1.0)

        self.assertAlmostEqual(outcome["max_return_2h"], 0.4)
        self.assertAlmostEqual(outcome["max_return_6h"], 0.6)
        self.assertAlmostEqual(outcome["max_return_24h"], 1.2)
        self.assertAlmostEqual(outcome["min_return_6h"], -0.3)
        self.assertEqual(outcome["hit_2h_up20"], 1)
        self.assertEqual(outcome["hit_6h_up50"], 1)
        self.assertEqual(outcome["hit_24h_up100"], 1)
        self.assertEqual(outcome["hit_6h_dd30"], 1)
        self.assertEqual(outcome["sample_count_2h"], 2)
        self.assertEqual(outcome["sample_count_6h"], 3)
        self.assertEqual(outcome["sample_count_24h"], 4)
        self.assertEqual(outcome["outcome_source"], "geckoterminal_hourly")
        self.assertEqual(outcome["base_price_source"], "signal_feature_price")
        self.assertAlmostEqual(outcome["base_price_usd"], 1.0)
        self.assertAlmostEqual(outcome["gecko_base_close_usd"], 1.0)
        self.assertAlmostEqual(outcome["price_divergence_pct"], 0.0)
        self.assertNotIn("price_source_divergence_gt_10pct", outcome["quality_flags"])

    def test_hourly_outcome_records_price_source_divergence(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 30, tzinfo=timezone.utc)
        rows = [
            _ohlcv_row(observed_at - timedelta(minutes=30), high=0.82, low=0.78, close=0.8),
            _ohlcv_row(observed_at + timedelta(minutes=30), high=1.2, low=0.95, close=1.0),
            _ohlcv_row(observed_at + timedelta(hours=1, minutes=30), high=1.3, low=0.9, close=1.1),
        ]

        outcome = compute_prediction_outcome_from_ohlcv(rows, observed_at=observed_at, base_price=1.0)

        self.assertEqual(outcome["base_price_source"], "signal_feature_price")
        self.assertAlmostEqual(outcome["base_price_usd"], 1.0)
        self.assertAlmostEqual(outcome["gecko_base_close_usd"], 0.8)
        self.assertAlmostEqual(outcome["price_divergence_pct"], 0.25)
        self.assertIn("price_source_divergence_gt_10pct", outcome["quality_flags"])
        self.assertIn("partial_6h_ohlcv", outcome["quality_flags"])
        self.assertIn("partial_24h_ohlcv", outcome["quality_flags"])

    def test_hourly_outcome_fetches_once_then_reuses_cached_external_rows(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 30, tzinfo=timezone.utc)
        rows = [
            _ohlcv_row(observed_at - timedelta(minutes=30), high=1.05, low=0.95, close=1.0),
            _ohlcv_row(observed_at + timedelta(minutes=30), high=1.25, low=0.85, close=1.1),
            _ohlcv_row(observed_at + timedelta(hours=1, minutes=30), high=1.4, low=0.9, close=1.2),
            _ohlcv_row(observed_at + timedelta(hours=23, minutes=30), high=2.2, low=1.1, close=2.0),
        ]
        stub = _StubGeckoClient(rows)

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            try:
                first = compute_prediction_outcome_with_hourly_ohlcv(
                    repo,
                    pair_address="0xpool",
                    observed_at=observed_at,
                    feature_json=json_dumps({"price_usd": 1.0}),
                    network="bsc",
                    gecko_client=stub,
                    now=observed_at + timedelta(hours=OUTCOME_BEFORE_MARGIN_HOURS, minutes=1),
                )
                second = compute_prediction_outcome_with_hourly_ohlcv(
                    repo,
                    pair_address="0xpool",
                    observed_at=observed_at,
                    feature_json=json_dumps({"price_usd": 1.0}),
                    network="bsc",
                    gecko_client=stub,
                    now=observed_at + timedelta(hours=OUTCOME_BEFORE_MARGIN_HOURS, minutes=2),
                )

                before_timestamp = int((observed_at + timedelta(hours=OUTCOME_BEFORE_MARGIN_HOURS)).timestamp())
                self.assertEqual(stub.calls, 1)
                self.assertEqual(stub.last_request["limit"], OUTCOME_OHLCV_LIMIT)
                self.assertEqual(stub.last_request["before_timestamp"], before_timestamp)
                self.assertIsNotNone(first)
                self.assertEqual(first, second)
                self.assertEqual(
                    repo.get_external_ohlcv_fetch_row_count(
                        network="bsc",
                        pool_address="0xpool",
                        timeframe="hour",
                        aggregate=1,
                        limit=OUTCOME_OHLCV_LIMIT,
                        before_timestamp=before_timestamp,
                    ),
                    len(rows),
                )
            finally:
                repo.close()

    def test_predictions_needing_outcomes_include_signal_features(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            try:
                signal_id = repo.insert_signal(
                    "0xpool",
                    "0xtoken",
                    SignalDecision(
                        observed_at=observed_at,
                        strategy_version="v1",
                        score=66,
                        pair_state="focused",
                        should_alert=False,
                        reasons=("h1_volume_support",),
                        risk_flags=(),
                        features={"price_usd": 1.23},
                    ),
                )
                repo.upsert_signal_prediction(
                    signal_id,
                    pair_address="0xpool",
                    token_address="0xtoken",
                    observed_at=observed_at,
                    prediction=PredictionResult(
                        predictor_version="p3",
                        prob_2h_up20=0.1,
                        prob_6h_up50=0.1,
                        prob_24h_up100=0.1,
                        risk_6h_dd30=0.1,
                        opportunity_score=55,
                        stage="early",
                        reasons=("prediction_low_opportunity",),
                    ),
                )

                rows = repo.list_predictions_needing_outcomes(observed_at + timedelta(hours=25), limit=10)

                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["feature_json"], json_dumps({"price_usd": 1.23}))
            finally:
                repo.close()

    def test_predictions_needing_outcomes_can_include_existing_rows_missing_quality(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            try:
                signal_id = repo.insert_signal(
                    "0xpool",
                    "0xtoken",
                    SignalDecision(
                        observed_at=observed_at,
                        strategy_version="v1",
                        score=66,
                        pair_state="focused",
                        should_alert=False,
                        reasons=("h1_volume_support",),
                        risk_flags=(),
                        features={"price_usd": 1.23},
                    ),
                )
                repo.upsert_signal_prediction(
                    signal_id,
                    pair_address="0xpool",
                    token_address="0xtoken",
                    observed_at=observed_at,
                    prediction=PredictionResult(
                        predictor_version="p3",
                        prob_2h_up20=0.1,
                        prob_6h_up50=0.1,
                        prob_24h_up100=0.1,
                        risk_6h_dd30=0.1,
                        opportunity_score=55,
                        stage="early",
                        reasons=("prediction_low_opportunity",),
                    ),
                )
                repo.upsert_prediction_outcome(
                    signal_id,
                    {
                        "max_return_2h": 0.1,
                        "max_return_6h": 0.2,
                        "max_return_24h": 0.3,
                        "min_return_6h": -0.1,
                        "sample_count_2h": 2,
                        "sample_count_6h": 6,
                        "sample_count_24h": 24,
                    },
                    evaluated_at=observed_at + timedelta(hours=25),
                )

                default_rows = repo.list_predictions_needing_outcomes(observed_at + timedelta(hours=26), limit=10)
                quality_rows = repo.list_predictions_needing_outcomes(
                    observed_at + timedelta(hours=26),
                    limit=10,
                    include_missing_quality=True,
                )

                self.assertEqual(default_rows, [])
                self.assertEqual(len(quality_rows), 1)
                self.assertEqual(quality_rows[0]["signal_id"], signal_id)
            finally:
                repo.close()


def _ohlcv_row(at: datetime, *, high: float, low: float, close: float) -> dict[str, float]:
    return {
        "ts": int(at.timestamp()),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100.0,
    }


if __name__ == "__main__":
    unittest.main()
