from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.scheduled_backtest import (
    build_scheduled_backtest_report,
    run_scheduled_backtest_cycle,
    render_scheduled_backtest_markdown,
    write_scheduled_backtest_outputs,
)
from token_meme_monitor.utils import json_dumps


class ScheduledBacktestTests(unittest.TestCase):
    def test_report_flags_missed_gainers_and_chase_signals(self) -> None:
        observed_at = datetime(2026, 4, 29, 0, 0, tzinfo=timezone.utc)
        rows = [
            _row(
                observed_at,
                "0xpair-good",
                "0xtoken-good",
                token_symbol="GOOD",
                score=72,
                short_momentum_score=64,
                max_return_2h=0.62,
                max_return_24h=1.25,
                prediction_reasons=["prediction_price_accelerating"],
            ),
            _row(
                observed_at + timedelta(minutes=20),
                "0xpair-good",
                "0xtoken-good",
                token_symbol="GOOD",
                score=70,
                short_momentum_score=60,
                max_return_2h=0.40,
                max_return_24h=0.90,
                prediction_reasons=["prediction_price_accelerating"],
            ),
            _row(
                observed_at + timedelta(hours=3),
                "0xpair-miss",
                "0xtoken-miss",
                token_symbol="MISS",
                score=28,
                short_momentum_score=22,
                max_return_2h=0.56,
                max_return_24h=0.88,
                prediction_reasons=["prediction_low_opportunity"],
            ),
            _row(
                observed_at + timedelta(hours=6),
                "0xpair-late",
                "0xtoken-late",
                token_symbol="LATE",
                score=86,
                short_momentum_score=76,
                max_return_2h=0.28,
                max_return_24h=0.34,
                feature_overrides={"h1_return_live": 0.92, "h4_return_live": 1.8},
                prediction_reasons=["prediction_h1_overextended", "prediction_h4_overextended"],
                stage="exhaustion",
            ),
            _row(
                observed_at + timedelta(hours=9),
                "0xpair-low",
                "0xtoken-low",
                token_symbol="LOW",
                score=40,
                short_momentum_score=30,
                max_return_2h=0.02,
                max_return_24h=0.04,
            ),
        ]

        report = build_scheduled_backtest_report(
            rows,
            train_ratio=0.5,
            top_gainers_limit=3,
            strong_gainer_return_threshold=0.20,
            max_price_divergence_pct=0.10,
            generated_at=observed_at,
        )

        self.assertEqual(report["summary"]["top_gainer_count"], 3)
        self.assertIn("strategy_feedback", report)
        self.assertIn("recommendation_count", report["strategy_feedback"])
        self.assertEqual([item["token_symbol"] for item in report["top_gainers"]], ["GOOD", "MISS", "LATE"])
        self.assertEqual(report["top_gainers"][0]["token_symbol"], "GOOD")
        self.assertEqual(report["missed_strong_gainers"][0]["token_symbol"], "MISS")
        self.assertIn("短线机会分低于45", report["missed_strong_gainers"][0]["miss_reasons"])
        self.assertEqual(report["chase_signals"][0]["token_symbol"], "LATE")
        self.assertIn("信号出现时已过热", report["chase_signals"][0]["chase_reasons"])

    def test_markdown_and_outputs_include_operational_sections(self) -> None:
        observed_at = datetime(2026, 4, 29, 0, 0, tzinfo=timezone.utc)
        report = build_scheduled_backtest_report(
            [_row(observed_at, "0xpair", "0xtoken", token_symbol="ONE", max_return_2h=0.25)],
            train_ratio=0.5,
            top_gainers_limit=5,
            generated_at=observed_at,
        )

        markdown = render_scheduled_backtest_markdown(report)

        self.assertIn("# Scheduled Backtest Report", markdown)
        self.assertIn("## 核心发现", markdown)
        self.assertIn("## 涨幅榜", markdown)
        self.assertIn("## 漏抓分析", markdown)
        self.assertIn("## 追高风险", markdown)

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "report.json"
            md_path = Path(tmpdir) / "report.md"

            write_scheduled_backtest_outputs(report, json_path=str(json_path), markdown_path=str(md_path))

            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("Scheduled Backtest Report", md_path.read_text(encoding="utf-8"))

    def test_cycle_records_failure_state_before_reraising(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            repo.close()

            with self.assertRaises(RuntimeError):
                with patch(
                    "token_meme_monitor.scheduled_backtest.build_scheduled_backtest_report",
                    side_effect=RuntimeError("boom"),
                ):
                    run_scheduled_backtest_cycle(
                        database_path=str(database_path),
                        chain_id="bsc",
                        json_out=str(Path(tmpdir) / "scheduled.json"),
                        md_out=str(Path(tmpdir) / "scheduled.md"),
                        skip_refresh_outcomes=True,
                    )

            repo = MonitorRepository(str(database_path))
            repo.initialize()
            state = repo.get_external_json_cache("runtime:scheduled_backtest:last_run")
            repo.close()
            self.assertIsNotNone(state)
            self.assertEqual(state["value"]["status"], "failure")
            self.assertIn("RuntimeError: boom", state["value"]["error"])


def _row(
    observed_at: datetime,
    pair_address: str,
    token_address: str,
    *,
    token_symbol: str = "TKN",
    score: int = 65,
    short_momentum_score: int = 50,
    max_return_2h: float = 0.0,
    max_return_6h: float | None = None,
    max_return_24h: float | None = None,
    sample_count_2h: int = 2,
    sample_count_6h: int = 6,
    sample_count_24h: int = 24,
    prediction_reasons: list[str] | None = None,
    feature_overrides: dict | None = None,
    stage: str = "acceleration",
) -> dict:
    features = {
        "volume_to_liquidity_h1": 1.2,
        "h1_return_live": 0.18,
        "h24_return_live": 0.4,
        "liquidity_to_fdv": 0.02,
    }
    if feature_overrides:
        features.update(feature_overrides)
    max_return_6h = max_return_2h if max_return_6h is None else max_return_6h
    max_return_24h = max_return_6h if max_return_24h is None else max_return_24h
    return {
        "signal_id": f"{pair_address}-{int(observed_at.timestamp())}",
        "pair_address": pair_address,
        "token_address": token_address,
        "token_symbol": token_symbol,
        "token_name": f"{token_symbol} Token",
        "observed_at": observed_at.isoformat(timespec="seconds"),
        "score": score,
        "strategy_version": "v1",
        "pair_state": "focused" if score >= 65 else "watching",
        "should_alert": 1 if score >= 80 else 0,
        "reasons": json_dumps(["h1_volume_support"]),
        "risk_flags": json_dumps([]),
        "feature_json": json_dumps(features),
        "token_metadata_json": json_dumps({"alpha_score": 100}),
        "stage": stage,
        "short_momentum_score": short_momentum_score,
        "opportunity_score": short_momentum_score,
        "prob_2h_up20": short_momentum_score / 1000,
        "prob_6h_up50": 0.03,
        "prob_24h_up100": 0.04,
        "risk_6h_dd30": 0.02,
        "prediction_reasons": json_dumps(prediction_reasons or []),
        "max_return_2h": max_return_2h,
        "max_return_6h": max_return_6h,
        "max_return_24h": max_return_24h,
        "min_return_6h": -0.02,
        "hit_2h_up20": 1 if max_return_2h >= 0.20 else 0,
        "hit_6h_up50": 1 if max_return_6h >= 0.50 else 0,
        "hit_24h_up100": 1 if max_return_24h >= 1.00 else 0,
        "hit_6h_dd30": 0,
        "sample_count_2h": sample_count_2h,
        "sample_count_6h": sample_count_6h,
        "sample_count_24h": sample_count_24h,
        "price_divergence_pct": 0.01,
        "quality_flags_json": json_dumps([]),
    }


if __name__ == "__main__":
    unittest.main()
