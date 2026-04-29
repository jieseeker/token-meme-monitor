from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from token_meme_monitor.models import SignalDecision
from token_meme_monitor.predictions import PREDICTOR_VERSION, build_prediction_calibration, build_prediction_result
from token_meme_monitor.utils import json_dumps


class PredictionTests(unittest.TestCase):
    def test_hot_early_alpha_signal_has_opportunity(self) -> None:
        decision = SignalDecision(
            observed_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
            strategy_version="v1",
            score=65,
            pair_state="focused",
            should_alert=False,
            reasons=("h1_volume_support",),
            risk_flags=("fdv_liquidity_stretched",),
            features={
                "volume_to_liquidity_h1": 12.0,
                "volume_impulse_vs_prev24h": 3.5,
                "volume_impulse_vs_prev72h": 2.2,
                "buy_sell_ratio_m5": 2.0,
                "h1_return_live": 0.18,
                "h4_return_live": 0.35,
                "h24_return_live": 0.4,
                "price_change_m5": 2.0,
                "price_change_h1": 18.0,
                "liquidity_to_fdv": 0.00003,
                "market_cap": 120_000_000,
            },
        )

        prediction = build_prediction_result(
            decision,
            token_metadata={"alpha_score": 111, "holder_count": 54_862, "binance_futures_listed": True},
        )

        self.assertIn(prediction.stage, {"early", "acceleration", "late"})
        self.assertGreaterEqual(prediction.short_momentum_score, 50)
        self.assertEqual(prediction.opportunity_score, prediction.short_momentum_score)
        self.assertGreaterEqual(prediction.short_momentum_score, prediction.continuation_score)
        self.assertGreaterEqual(prediction.continuation_score, prediction.breakout_score)
        self.assertGreater(prediction.prob_6h_up50, 0.08)
        self.assertIn("prediction_alpha_hot", prediction.reasons)
        self.assertIn("prediction_short_momentum_opportunity", prediction.reasons)

    def test_overextended_reversal_raises_drawdown_risk(self) -> None:
        decision = SignalDecision(
            observed_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
            strategy_version="v1",
            score=78,
            pair_state="focused",
            should_alert=False,
            reasons=("h1_volume_support",),
            risk_flags=("sell_pressure", "fdv_liquidity_stretched"),
            features={
                "volume_to_liquidity_h1": 8.0,
                "volume_impulse_vs_prev24h": 1.0,
                "volume_impulse_vs_prev72h": 0.8,
                "buy_sell_ratio_m5": 0.7,
                "h1_return_live": 1.2,
                "h4_return_live": 2.4,
                "h24_return_live": 4.0,
                "price_change_m5": -8.0,
                "price_change_h1": 140.0,
                "liquidity_to_fdv": 0.00002,
                "market_cap": 180_000_000,
            },
        )

        prediction = build_prediction_result(
            decision,
            token_metadata={"alpha_score": 111, "holder_count": 54_862, "binance_futures_listed": True},
        )

        self.assertEqual(prediction.stage, "exhaustion")
        self.assertGreater(prediction.risk_6h_dd30, prediction.prob_6h_up50)
        self.assertLess(prediction.continuation_score, prediction.short_momentum_score)
        self.assertIn("prediction_m5_reversal", prediction.reasons)

    def test_empirical_calibration_lowers_probability_for_weak_matching_bucket(self) -> None:
        features = {
            "volume_to_liquidity_h1": 12.0,
            "volume_impulse_vs_prev24h": 3.5,
            "volume_impulse_vs_prev72h": 2.2,
            "buy_sell_ratio_m5": 2.0,
            "h1_return_live": 0.18,
            "h4_return_live": 0.35,
            "h24_return_live": 0.4,
            "price_change_m5": 2.0,
            "price_change_h1": 18.0,
            "liquidity_to_fdv": 0.02,
            "market_cap": 900_000,
        }
        metadata = {"alpha_score": 111, "holder_count": 54_862}
        decision = SignalDecision(
            observed_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
            strategy_version="v1",
            score=65,
            pair_state="focused",
            should_alert=False,
            reasons=("h1_volume_support",),
            risk_flags=(),
            features=features,
        )
        base_prediction = build_prediction_result(decision, token_metadata=metadata)
        calibration_rows = [
            {
                "score": 65,
                "stage": base_prediction.stage,
                "feature_json": json_dumps(features),
                "token_metadata_json": json_dumps(metadata),
                "sample_count_2h": 2,
                "sample_count_6h": 6,
                "sample_count_24h": 24,
                "hit_2h_up20": 0,
                "hit_6h_up50": 0,
                "hit_24h_up100": 0,
                "hit_6h_dd30": 0,
            }
            for _ in range(20)
        ]
        calibration = build_prediction_calibration(calibration_rows)

        calibrated = build_prediction_result(decision, token_metadata=metadata, calibration=calibration)

        self.assertEqual(calibrated.predictor_version, PREDICTOR_VERSION)
        self.assertLess(calibrated.prob_6h_up50, base_prediction.prob_6h_up50)
        self.assertIn("prediction_empirical_calibrated", calibrated.reasons)
        self.assertIn("prediction_empirical_lowered", calibrated.reasons)

    def test_empirical_calibration_ignores_low_quality_outcomes(self) -> None:
        features = {
            "h1_return_live": 0.18,
            "h24_return_live": 0.4,
            "volume_to_liquidity_h1": 1.2,
        }
        metadata = {"alpha_score": 111}

        def row(pair_address: str, *, source: str, divergence: float | None, hit_2h: int) -> dict:
            return {
                "pair_address": pair_address,
                "token_address": f"0xtoken{pair_address[-1]}",
                "observed_at": datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc).isoformat(timespec="seconds"),
                "score": 65,
                "stage": "acceleration",
                "feature_json": json_dumps(features),
                "token_metadata_json": json_dumps(metadata),
                "outcome_source": source,
                "price_divergence_pct": divergence,
                "quality_flags_json": json_dumps([]),
                "sample_count_2h": 2,
                "sample_count_6h": 6,
                "sample_count_24h": 24,
                "hit_2h_up20": hit_2h,
                "hit_6h_up50": hit_2h,
                "hit_24h_up100": hit_2h,
                "hit_6h_dd30": 0,
            }

        calibration = build_prediction_calibration(
            [
                row("0xpair1", source="geckoterminal_hourly", divergence=0.02, hit_2h=0),
                row("0xpair2", source="geckoterminal_hourly", divergence=0.25, hit_2h=1),
                row("0xpair3", source="local_snapshots", divergence=None, hit_2h=1),
            ]
        )

        self.assertEqual(calibration.total_rows, 1)
        self.assertEqual(calibration.buckets[("global",)].sample_2h_up20, 1)
        self.assertEqual(calibration.buckets[("global",)].hit_2h_up20, 0)

    def test_empirical_calibration_counts_nearby_duplicate_snapshots_once(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        features = {
            "h1_return_live": 0.18,
            "h24_return_live": 0.4,
            "volume_to_liquidity_h1": 1.2,
        }
        metadata = {"alpha_score": 111}

        def row(at: datetime, *, hit_2h: int) -> dict:
            return {
                "pair_address": "0xpair",
                "token_address": "0xtoken",
                "observed_at": at.isoformat(timespec="seconds"),
                "score": 65,
                "stage": "acceleration",
                "feature_json": json_dumps(features),
                "token_metadata_json": json_dumps(metadata),
                "sample_count_2h": 2,
                "sample_count_6h": 6,
                "sample_count_24h": 24,
                "hit_2h_up20": hit_2h,
                "hit_6h_up50": 0,
                "hit_24h_up100": 0,
                "hit_6h_dd30": 0,
            }

        calibration = build_prediction_calibration(
            [
                row(observed_at, hit_2h=1),
                row(observed_at + timedelta(minutes=20), hit_2h=0),
                row(observed_at + timedelta(hours=3), hit_2h=0),
            ]
        )

        self.assertEqual(calibration.total_rows, 2)
        self.assertEqual(calibration.buckets[("global",)].sample_2h_up20, 2)
        self.assertEqual(calibration.buckets[("global",)].hit_2h_up20, 1)

    def test_sparse_empirical_calibration_does_not_raise_upside_probability(self) -> None:
        features = {
            "volume_to_liquidity_h1": 1.2,
            "h1_return_live": 0.18,
            "h24_return_live": 0.4,
            "liquidity_to_fdv": 0.02,
        }
        metadata = {"alpha_score": 111}
        decision = SignalDecision(
            observed_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
            strategy_version="v1",
            score=65,
            pair_state="focused",
            should_alert=False,
            reasons=("h1_volume_support",),
            risk_flags=(),
            features=features,
        )
        base_prediction = build_prediction_result(decision, token_metadata=metadata)
        calibration_rows = [
            {
                "pair_address": f"0xpair{index}",
                "observed_at": datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc).isoformat(timespec="seconds"),
                "score": 65,
                "stage": base_prediction.stage,
                "feature_json": json_dumps(features),
                "token_metadata_json": json_dumps(metadata),
                "sample_count_2h": 2,
                "sample_count_6h": 6,
                "sample_count_24h": 24,
                "hit_2h_up20": 1,
                "hit_6h_up50": 1,
                "hit_24h_up100": 1,
                "hit_6h_dd30": 0,
            }
            for index in range(20)
        ]
        calibration = build_prediction_calibration(calibration_rows)

        calibrated = build_prediction_result(decision, token_metadata=metadata, calibration=calibration)

        self.assertAlmostEqual(calibrated.prob_2h_up20, base_prediction.prob_2h_up20)
        self.assertAlmostEqual(calibrated.prob_6h_up50, base_prediction.prob_6h_up50)
        self.assertAlmostEqual(calibrated.prob_24h_up100, base_prediction.prob_24h_up100)
        self.assertNotIn("prediction_empirical_raised", calibrated.reasons)


if __name__ == "__main__":
    unittest.main()
