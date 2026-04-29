from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from dashboard.view_models import (
    build_database_revision_key,
    build_conclusion,
    build_prediction_confidence,
    build_latest_signal_context,
    build_overview_frame,
    filter_overview_frame,
    metric_value,
    resolve_selected_pair,
)


class DashboardViewModelTests(unittest.TestCase):
    def test_database_revision_key_includes_sqlite_wal_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            database_path.write_text("db", encoding="utf-8")
            initial_revision = build_database_revision_key(str(database_path))

            wal_path = Path(f"{database_path}-wal")
            wal_path.write_text("wal update", encoding="utf-8")
            wal_revision = build_database_revision_key(str(database_path))

            self.assertNotEqual(initial_revision, wal_revision)
            self.assertIn(("wal", wal_path.stat().st_mtime_ns, wal_path.stat().st_size), wal_revision)

    def test_filter_overview_frame_uses_dynamic_activity_window(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        raw_overview_df = pd.DataFrame(
            [
                {
                    "pair_address": "0xpair",
                    "token_address": "0xtoken",
                    "token_symbol": "MEME",
                    "token_name": "Meme",
                    "token_metadata_json": '{"is_binance_alpha": true, "holder_count": 1200, "alpha_score": 80, "alpha_liquidity": 20000}',
                    "quote_symbol": "WBNB",
                    "state": "watching",
                    "snapshot_observed_at": observed_at.isoformat(),
                    "price_usd": 1.2,
                    "liquidity_usd": 18000,
                    "market_cap": 250000,
                    "fdv": 300000,
                    "volume_h1": 20000,
                    "volume_m5": 5000,
                    "last_score": 82,
                }
            ]
        )

        overview_df = build_overview_frame(raw_overview_df)
        _, filtered_live = filter_overview_frame(
            overview_df,
            min_signal=65,
            min_holders=1000,
            min_liquidity=15000,
            only_with_market_data=True,
            now=pd.Timestamp(observed_at + timedelta(minutes=10)),
        )
        _, filtered_stale = filter_overview_frame(
            overview_df,
            min_signal=65,
            min_holders=1000,
            min_liquidity=15000,
            only_with_market_data=True,
            now=pd.Timestamp(observed_at + timedelta(minutes=20)),
        )

        self.assertEqual(len(overview_df), 1)
        self.assertEqual(len(filtered_live), 1)
        self.assertEqual(len(filtered_stale), 0)

    def test_build_overview_frame_keeps_largest_pool_when_token_has_no_pair_signal(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        base_row = {
            "token_address": "0xtoken",
            "token_symbol": "MM",
            "token_name": "Momo",
            "token_metadata_json": '{"is_binance_alpha": true, "holder_count": 1200, "alpha_score": 80, "alpha_liquidity": 20000}',
            "state": "watching",
            "snapshot_observed_at": observed_at.isoformat(),
            "price_usd": 1.2,
            "market_cap": 250000,
            "fdv": 300000,
            "volume_h1": 20000,
            "volume_m5": 5000,
            "last_score": 0,
            "last_pair_state": "watching",
        }
        raw_overview_df = pd.DataFrame(
            [
                {
                    **base_row,
                    "pair_address": "0xsmall",
                    "quote_symbol": "WBNB",
                    "dex_id": "pancakeswap",
                    "liquidity_usd": 18000,
                },
                {
                    **base_row,
                    "pair_address": "0xbig",
                    "quote_symbol": "USDT",
                    "dex_id": "uniswap",
                    "liquidity_usd": 64000,
                },
            ]
        )

        overview_df = build_overview_frame(raw_overview_df)

        self.assertEqual(overview_df["pair_address"].tolist(), ["0xbig"])
        self.assertEqual(float(overview_df.iloc[0]["pair_pool_liquidity"]), 64000)

    def test_build_overview_frame_prefers_highest_signal_pair_over_largest_pool(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        base_row = {
            "token_address": "0xtoken",
            "token_symbol": "BSB",
            "token_name": "Block Street",
            "token_metadata_json": '{"is_binance_alpha": true, "holder_count": 54862, "alpha_score": 111, "alpha_liquidity": 2000000}',
            "state": "watching",
            "snapshot_observed_at": observed_at.isoformat(),
            "price_usd": 1.2,
            "market_cap": 250000,
            "fdv": 300000,
            "volume_h1": 20000,
            "volume_m5": 5000,
        }
        raw_overview_df = pd.DataFrame(
            [
                {
                    **base_row,
                    "pair_address": "0xbig",
                    "quote_symbol": "USDT",
                    "dex_id": "uniswap",
                    "liquidity_usd": 2_000_000,
                    "last_score": 0,
                    "last_pair_state": "watching",
                },
                {
                    **base_row,
                    "pair_address": "0xactive",
                    "quote_symbol": "USDT",
                    "dex_id": "uniswap",
                    "liquidity_usd": 23_000,
                    "last_score": 72,
                    "last_pair_state": "focused",
                },
            ]
        )

        overview_df = build_overview_frame(raw_overview_df)

        self.assertEqual(overview_df["pair_address"].tolist(), ["0xactive"])
        self.assertTrue(bool(overview_df.iloc[0]["has_pair_signal"]))
        self.assertEqual(float(overview_df.iloc[0]["pair_pool_liquidity"]), 23000)

    def test_build_overview_frame_prefers_prediction_opportunity_pair(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        base_row = {
            "token_address": "0xtoken",
            "token_symbol": "BSB",
            "token_name": "Block Street",
            "token_metadata_json": '{"is_binance_alpha": true, "holder_count": 54862, "alpha_score": 111}',
            "state": "watching",
            "snapshot_observed_at": observed_at.isoformat(),
            "price_usd": 1.2,
            "market_cap": 250000,
            "fdv": 300000,
            "volume_h1": 20000,
            "volume_m5": 5000,
            "last_pair_state": "watching",
            "last_score": 20,
        }
        raw_overview_df = pd.DataFrame(
            [
                {
                    **base_row,
                    "pair_address": "0xbig",
                    "quote_symbol": "USDT",
                    "dex_id": "uniswap",
                    "liquidity_usd": 2_000_000,
                    "prediction_opportunity_score": 20,
                },
                {
                    **base_row,
                    "pair_address": "0xopportunity",
                    "quote_symbol": "USDT",
                    "dex_id": "uniswap",
                    "liquidity_usd": 23_000,
                    "prediction_opportunity_score": 72,
                },
            ]
        )

        overview_df = build_overview_frame(raw_overview_df)

        self.assertEqual(overview_df["pair_address"].tolist(), ["0xopportunity"])
        self.assertEqual(int(overview_df.iloc[0]["prediction_opportunity_score"]), 72)

    def test_build_overview_frame_prefers_live_pair_before_stale_opportunity(self) -> None:
        now = datetime.now(timezone.utc)
        base_row = {
            "token_address": "0xtoken",
            "token_symbol": "MM",
            "token_name": "Meme Momentum",
            "token_metadata_json": '{"is_binance_alpha": true, "holder_count": 54862, "alpha_score": 111}',
            "state": "watching",
            "price_usd": 1.2,
            "market_cap": 250000,
            "fdv": 300000,
            "volume_h1": 20000,
            "volume_m5": 5000,
            "last_pair_state": "watching",
        }
        raw_overview_df = pd.DataFrame(
            [
                {
                    **base_row,
                    "pair_address": "0xstale",
                    "quote_symbol": "USDT",
                    "dex_id": "uniswap",
                    "snapshot_observed_at": (now - timedelta(minutes=20)).isoformat(),
                    "liquidity_usd": 2_000_000,
                    "last_score": 20,
                    "prediction_opportunity_score": 72,
                },
                {
                    **base_row,
                    "pair_address": "0xlive",
                    "quote_symbol": "WBNB",
                    "dex_id": "pancakeswap",
                    "snapshot_observed_at": (now - timedelta(minutes=1)).isoformat(),
                    "liquidity_usd": 23_000,
                    "last_score": 70,
                    "prediction_opportunity_score": 30,
                },
            ]
        )

        overview_df = build_overview_frame(raw_overview_df)
        _, filtered = filter_overview_frame(
            overview_df,
            min_signal=65,
            min_holders=1000,
            min_liquidity=15000,
            only_with_market_data=True,
            now=pd.Timestamp(now),
        )

        self.assertEqual(overview_df["pair_address"].tolist(), ["0xlive"])
        self.assertEqual(filtered["pair_address"].tolist(), ["0xlive"])

    def test_prediction_confidence_marks_sparse_empirical_sample(self) -> None:
        confidence = build_prediction_confidence(
            {
                "prediction_reasons": '["prediction_empirical_sparse", "prediction_short_momentum_opportunity"]',
                "prediction_short_momentum_score": 46,
            }
        )

        self.assertEqual(confidence.title, "历史样本不足")
        self.assertEqual(confidence.tone, "neutral")
        self.assertIn("规则概率", confidence.body)
        self.assertIn("样本不足", confidence.chips)
        self.assertIn("校准样本不足", confidence.evidence)

    def test_prediction_confidence_warns_when_high_score_is_not_calibrated(self) -> None:
        confidence = build_prediction_confidence(
            {
                "prediction_reasons": '["prediction_high_opportunity"]',
                "prediction_short_momentum_score": 58,
            }
        )

        self.assertEqual(confidence.title, "高分段仍需复核")
        self.assertEqual(confidence.tone, "warn")
        self.assertIn("样本", confidence.body)
        self.assertIn("未命中足量历史桶", confidence.evidence)

    def test_prediction_confidence_marks_empirical_calibration(self) -> None:
        confidence = build_prediction_confidence(
            {
                "prediction_reasons": '["prediction_empirical_calibrated", "prediction_empirical_lowered"]',
                "prediction_short_momentum_score": 52,
            }
        )

        self.assertEqual(confidence.title, "已叠加历史校准")
        self.assertEqual(confidence.tone, "accent")
        self.assertIn("历史命中率", confidence.body)
        self.assertIn("历史校准下调", confidence.evidence)

    def test_resolve_selected_pair_prefers_query_param_over_session_state(self) -> None:
        selected_pair = resolve_selected_pair(
            ["0xold", "0xquery"],
            widget_selected_pair="0xold",
            session_selected_pair="0xold",
            query_selected_pair="0xquery",
        )

        self.assertEqual(selected_pair, "0xquery")

    def test_resolve_selected_pair_allows_widget_to_override_synced_query_param(self) -> None:
        selected_pair = resolve_selected_pair(
            ["0xold", "0xclicked"],
            widget_selected_pair="0xclicked",
            session_selected_pair="0xold",
            query_selected_pair="0xold",
            query_has_priority=False,
        )

        self.assertEqual(selected_pair, "0xclicked")

    def test_filter_overview_frame_allows_prediction_opportunity_to_pass_signal_gate(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        raw_overview_df = pd.DataFrame(
            [
                {
                    "pair_address": "0xpair",
                    "token_address": "0xtoken",
                    "token_symbol": "MEME",
                    "token_name": "Meme",
                    "token_metadata_json": '{"is_binance_alpha": true, "holder_count": 1200, "alpha_score": 80, "alpha_liquidity": 20000}',
                    "quote_symbol": "WBNB",
                    "state": "watching",
                    "snapshot_observed_at": observed_at.isoformat(),
                    "price_usd": 1.2,
                    "liquidity_usd": 18000,
                    "market_cap": 250000,
                    "fdv": 300000,
                    "volume_h1": 20000,
                    "volume_m5": 5000,
                    "last_score": 20,
                    "prediction_opportunity_score": 72,
                }
            ]
        )

        overview_df = build_overview_frame(raw_overview_df)
        _, filtered = filter_overview_frame(
            overview_df,
            min_signal=65,
            min_holders=1000,
            min_liquidity=15000,
            only_with_market_data=True,
            now=pd.Timestamp(observed_at + timedelta(minutes=10)),
        )

        self.assertEqual(filtered["pair_address"].tolist(), ["0xpair"])

    def test_filter_overview_frame_uses_short_momentum_score_for_signal_gate(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        raw_overview_df = pd.DataFrame(
            [
                {
                    "pair_address": "0xpair",
                    "token_address": "0xtoken",
                    "token_symbol": "FAST",
                    "token_name": "Fast Token",
                    "token_metadata_json": '{"is_binance_alpha": true, "holder_count": 1200, "alpha_score": 80, "alpha_liquidity": 20000}',
                    "quote_symbol": "WBNB",
                    "state": "watching",
                    "snapshot_observed_at": observed_at.isoformat(),
                    "price_usd": 1.2,
                    "liquidity_usd": 18000,
                    "market_cap": 250000,
                    "fdv": 300000,
                    "volume_h1": 20000,
                    "volume_m5": 5000,
                    "last_score": 20,
                    "prediction_opportunity_score": 30,
                    "prediction_short_momentum_score": 68,
                    "prediction_continuation_score": 34,
                    "prediction_breakout_score": 12,
                }
            ]
        )

        overview_df = build_overview_frame(raw_overview_df)
        _, filtered = filter_overview_frame(
            overview_df,
            min_signal=65,
            min_holders=1000,
            min_liquidity=15000,
            only_with_market_data=True,
            now=pd.Timestamp(observed_at + timedelta(minutes=10)),
        )

        self.assertEqual(filtered["pair_address"].tolist(), ["0xpair"])
        self.assertEqual(int(filtered.iloc[0]["prediction_short_momentum_score"]), 68)

    def test_filter_overview_frame_prioritizes_high_risk_momentum_over_plain_opportunity(self) -> None:
        observed_at = datetime(2026, 4, 27, 6, 30, tzinfo=timezone.utc)
        base_row = {
            "token_metadata_json": '{"is_binance_alpha": true, "holder_count": 1200, "alpha_score": 111, "alpha_liquidity": 20000}',
            "quote_symbol": "USDT",
            "state": "watching",
            "snapshot_observed_at": observed_at.isoformat(),
            "price_usd": 1.2,
            "liquidity_usd": 25000,
            "market_cap": 250000,
            "fdv": 300000,
            "volume_h1": 20000,
            "volume_m5": 5000,
            "last_pair_state": "watching",
            "last_should_alert": 0,
        }
        raw_overview_df = pd.DataFrame(
            [
                {
                    **base_row,
                    "pair_address": "0xplain",
                    "token_address": "0xplain_token",
                    "token_symbol": "PLAIN",
                    "token_name": "Plain Opportunity",
                    "last_score": 20,
                    "prediction_opportunity_score": 85,
                    "last_risk_flags": "[]",
                    "last_reasons": "[]",
                    "prediction_reasons": "[]",
                    "last_feature_json": '{"volume_h1": 10000, "volume_to_liquidity_h1": 0.4, "price_change_h1": 2, "price_change_m5": 0.5}',
                },
                {
                    **base_row,
                    "pair_address": "0xprl_like",
                    "token_address": "0xprl_token",
                    "token_symbol": "PRL",
                    "token_name": "Perle Like",
                    "last_score": 60,
                    "prediction_opportunity_score": 63,
                    "last_risk_flags": '["fdv_liquidity_stretched","missing_project_metadata"]',
                    "last_reasons": '["h1_volume_support","volume_to_liquidity_breakout","alpha_hot_score","binance_futures_listed"]',
                    "prediction_reasons": '["prediction_price_accelerating"]',
                    "last_feature_json": '{"volume_h1": 150605.84, "volume_to_liquidity_h1": 5.5, "price_change_h1": 18.81, "price_change_m5": 0.39}',
                },
            ]
        )

        overview_df = build_overview_frame(raw_overview_df)
        _, filtered = filter_overview_frame(
            overview_df,
            min_signal=0,
            min_holders=1000,
            min_liquidity=15000,
            only_with_market_data=True,
            now=pd.Timestamp(observed_at + timedelta(minutes=2)),
        )

        self.assertEqual(filtered["pair_address"].tolist(), ["0xprl_like", "0xplain"])
        self.assertEqual(filtered.iloc[0]["display_tier_label"], "高风险动量")

    def test_filter_overview_frame_downgrades_overextended_opportunity(self) -> None:
        observed_at = datetime(2026, 4, 29, 8, 0, tzinfo=timezone.utc)
        base_row = {
            "token_metadata_json": '{"is_binance_alpha": true, "holder_count": 2200, "alpha_score": 96, "alpha_liquidity": 30000}',
            "quote_symbol": "USDT",
            "state": "watching",
            "snapshot_observed_at": observed_at.isoformat(),
            "price_usd": 1.2,
            "liquidity_usd": 42000,
            "market_cap": 750000,
            "fdv": 900000,
            "volume_h1": 160000,
            "volume_m5": 12000,
            "last_pair_state": "watching",
            "last_should_alert": 0,
            "last_risk_flags": "[]",
            "last_reasons": '["h1_volume_support","volume_to_liquidity_breakout"]',
        }
        raw_overview_df = pd.DataFrame(
            [
                {
                    **base_row,
                    "pair_address": "0xlaunch",
                    "token_address": "0xlaunch_token",
                    "token_symbol": "EARLY",
                    "token_name": "Early Launch",
                    "last_score": 58,
                    "prediction_opportunity_score": 62,
                    "prediction_short_momentum_score": 62,
                    "prediction_reasons": '["prediction_price_accelerating"]',
                    "last_feature_json": '{"volume_h1": 120000, "volume_to_liquidity_h1": 3.1, "price_change_h1": 18, "price_change_m5": 2, "h1_return_live": 0.18, "h4_return_live": 0.42, "h24_return_live": 0.8}',
                },
                {
                    **base_row,
                    "pair_address": "0xlate",
                    "token_address": "0xlate_token",
                    "token_symbol": "LATE",
                    "token_name": "Late Chase",
                    "last_score": 64,
                    "prediction_opportunity_score": 88,
                    "prediction_short_momentum_score": 88,
                    "prediction_reasons": '["prediction_h1_overextended","prediction_h4_overextended"]',
                    "last_feature_json": '{"volume_h1": 180000, "volume_to_liquidity_h1": 4.8, "price_change_h1": 92, "price_change_m5": 5, "h1_return_live": 0.92, "h4_return_live": 1.9, "h24_return_live": 2.4}',
                },
            ]
        )

        overview_df = build_overview_frame(raw_overview_df)
        _, filtered = filter_overview_frame(
            overview_df,
            min_signal=0,
            min_holders=1000,
            min_liquidity=15000,
            only_with_market_data=True,
            now=pd.Timestamp(observed_at + timedelta(minutes=2)),
        )

        self.assertEqual(filtered["pair_address"].tolist(), ["0xlaunch", "0xlate"])
        self.assertEqual(filtered.iloc[0]["display_tier_label"], "启动异动")
        self.assertEqual(filtered.iloc[1]["display_tier_label"], "已涨过多")

    def test_latest_signal_context_prefers_feature_values(self) -> None:
        overview_row = {
            "state": "watching",
            "price_usd": 9.9,
            "last_signal_at": "2026-04-25T12:00:00+00:00",
            "last_score": 77,
            "last_pair_state": "focused",
            "last_should_alert": 0,
            "last_reasons": '["h1_volume_support"]',
            "last_risk_flags": '["sell_pressure"]',
            "last_feature_json": '{"price_usd": 1.23, "liquidity_usd": 0}',
        }

        signal_context = build_latest_signal_context(overview_row)

        self.assertIsNotNone(signal_context)
        self.assertEqual(signal_context.score, 77)
        self.assertEqual(signal_context.pair_state, "focused")
        self.assertEqual(signal_context.reasons, ("h1_volume_support",))
        self.assertEqual(signal_context.risk_flags, ("sell_pressure",))
        self.assertEqual(metric_value(signal_context, overview_row, "price_usd"), 1.23)
        self.assertEqual(metric_value(signal_context, overview_row, "liquidity_usd"), 0)

    def test_build_conclusion_preserves_zero_liquidity_from_signal_features(self) -> None:
        overview_row = {
            "state": "watching",
            "holder_count": 2400,
            "alpha_score": 88,
            "alpha_liquidity": 50000,
            "alpha_price": 1.0,
            "alpha_market_cap": 750000,
            "price_usd": 1.0,
            "market_cap": 750000,
            "last_signal_at": "2026-04-25T12:00:00+00:00",
            "last_score": 88,
            "last_pair_state": "focused",
            "last_should_alert": 0,
            "last_reasons": '["h1_volume_support"]',
            "last_risk_flags": "[]",
            "last_feature_json": '{"price_usd": 1.0, "market_cap": 750000, "liquidity_usd": 0}',
        }

        signal_context = build_latest_signal_context(overview_row)
        conclusion = build_conclusion(
            signal_context,
            overview_row,
            explain_reason=lambda code: (code, code),
            explain_risk=lambda code: (code, code),
        )

        self.assertEqual(conclusion["title"], "暂不关注")


if __name__ == "__main__":
    unittest.main()
