from __future__ import annotations

import unittest
from datetime import datetime, timezone

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
        self.assertGreaterEqual(prediction.opportunity_score, 50)
        self.assertGreater(prediction.prob_6h_up50, 0.08)
        self.assertIn("prediction_alpha_hot", prediction.reasons)

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


if __name__ == "__main__":
    unittest.main()
