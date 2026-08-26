from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from token_meme_monitor.cli import main
from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.models import PairSnapshot, PredictionResult, SignalDecision
from token_meme_monitor.predictions import PREDICTOR_VERSION
from token_meme_monitor.utils import json_dumps


class CliTests(unittest.TestCase):
    def test_cleanup_data_recomputes_signal_when_only_token_metadata_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            token_address = "0xtoken"
            pair_address = "0xpair"
            repo.upsert_token(
                token_address,
                "MEME",
                "Meme",
                observed_at,
                {
                    "is_binance_alpha": True,
                    "alpha_score": 111,
                    "holder_count": "12000",
                },
            )
            repo.upsert_seed_pair(
                pair_address=pair_address,
                chain_id="bsc",
                token_address=token_address,
                token_symbol="MEME",
                token_name="Meme",
                quote_token_address="0xquote",
                quote_symbol="WBNB",
                token0_address=token_address,
                token1_address="0xquote",
                pair_created_at=observed_at - timedelta(hours=1),
                discovered_at=observed_at,
                metadata={"pair_url": "https://example.com/pair"},
            )
            repo.insert_snapshot(
                PairSnapshot(
                    pair_address=pair_address,
                    token_address=token_address,
                    token_symbol="MEME",
                    token_name="Meme",
                    quote_token_address="0xquote",
                    quote_symbol="WBNB",
                    observed_at=observed_at,
                    pair_created_at=observed_at - timedelta(hours=1),
                    dex_id="pancakeswap",
                    pair_url="https://example.com/pair",
                    price_usd=1.0,
                    price_native=0.01,
                    liquidity_usd=50_000,
                    fdv=500_000,
                    market_cap=450_000,
                    volume_m5=5_000,
                    volume_h1=20_000,
                    volume_h24=100_000,
                    buys_m5=10,
                    sells_m5=2,
                    buys_h1=40,
                    sells_h1=20,
                    price_change_m5=2,
                    price_change_h1=10,
                    price_change_h24=30,
                    website_count=1,
                    social_count=1,
                    boosts_active=0,
                    raw_payload={
                        "_data_quality": {
                            "flags": [],
                            "sources": {
                                "price_usd": "dexscreener",
                                "market_cap": "dexscreener",
                                "fdv": "dexscreener",
                                "liquidity_usd": "dexscreener",
                                "volume_h24": "dexscreener",
                            },
                        }
                    },
                ),
                age_minutes=60,
                risk_flags=[],
            )
            repo.insert_signal(
                pair_address,
                token_address,
                SignalDecision(
                    observed_at=observed_at,
                    strategy_version="v1",
                    score=1,
                    pair_state="watching",
                    should_alert=False,
                    reasons=(),
                    risk_flags=(),
                    features={"price_usd": 1.0},
                ),
            )
            repo.close()

            output = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "MONITOR_DATABASE_PATH": str(database_path),
                    "MONITOR_UNIVERSE": "binance_alpha",
                    "STRATEGY_VERSION": "v1",
                },
                clear=True,
            ):
                with redirect_stdout(output):
                    exit_code = main(["--env-file", "", "cleanup-data"])

            self.assertEqual(exit_code, 0)
            self.assertIn("tokens_updated=1", output.getvalue())
            self.assertIn("snapshots_updated=0", output.getvalue())
            self.assertIn("signals_updated=1", output.getvalue())

            repo = MonitorRepository(str(database_path))
            repo.initialize()
            signal_row = repo._conn.execute(
                """
                SELECT id, score, pair_state, reasons
                FROM signals
                WHERE pair_address = ? AND observed_at = ? AND strategy_version = ?
                """,
                (pair_address, observed_at.isoformat(timespec="seconds"), "v1"),
            ).fetchone()
            self.assertIsNotNone(signal_row)
            self.assertGreater(signal_row["score"], 1)
            self.assertIn("alpha_hot_score", signal_row["reasons"])
            self.assertIn("holder_depth", signal_row["reasons"])
            prediction_row = repo._conn.execute(
                "SELECT predictor_version, opportunity_score FROM signal_predictions WHERE signal_id = ?",
                (signal_row["id"],),
            ).fetchone()
            self.assertIsNotNone(prediction_row)
            self.assertEqual(prediction_row["predictor_version"], PREDICTOR_VERSION)
            token_metadata = repo.get_token_metadata(token_address)
            self.assertEqual(token_metadata["holder_count"], 12000)
            repo.close()

    def test_health_report_outputs_backend_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            repo.upsert_token(
                "0xtoken",
                "MEME",
                "Meme",
                observed_at,
                {"is_binance_alpha": True, "pair_seed_failed_at": observed_at.isoformat()},
            )
            repo.upsert_seed_pair(
                pair_address="0xpair",
                chain_id="bsc",
                token_address="0xtoken",
                token_symbol="MEME",
                token_name="Meme",
                quote_token_address="0xquote",
                quote_symbol="WBNB",
                token0_address="0xtoken",
                token1_address="0xquote",
                pair_created_at=observed_at - timedelta(hours=1),
                discovered_at=observed_at,
                metadata={},
            )
            repo.close()

            output = io.StringIO()
            with patch.dict(os.environ, {"MONITOR_DATABASE_PATH": str(database_path)}, clear=True):
                with redirect_stdout(output):
                    exit_code = main(["--env-file", "", "health-report", "--json"])

            self.assertEqual(exit_code, 0)
            text = output.getvalue()
            self.assertIn('"stale_active_pairs"', text)
            self.assertIn('"alpha_seed"', text)
            self.assertIn('"seed_failed": 1', text)

    def test_runtime_status_outputs_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            pid_dir = runtime_dir / "pids"
            log_dir = runtime_dir / "logs"
            pid_dir.mkdir()
            log_dir.mkdir()
            (pid_dir / "worker.pid").write_text("123\n", encoding="utf-8")
            (log_dir / "worker.log").write_text("worker ready\n", encoding="utf-8")

            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True):
                with patch("token_meme_monitor.runtime_status.is_pid_running", return_value=True):
                    with patch(
                        "token_meme_monitor.runtime_status.get_process_command",
                        return_value="/tmp/python -m token_meme_monitor run-worker",
                    ):
                        with redirect_stdout(output):
                            exit_code = main(
                                [
                                    "--env-file",
                                    "",
                                    "runtime-status",
                                    "--runtime-dir",
                                    str(runtime_dir),
                                    "--json",
                                ]
                            )

            self.assertEqual(exit_code, 0)
            text = output.getvalue()
            self.assertIn('"services"', text)
            self.assertIn('"name": "worker"', text)
            self.assertIn('"state": "running"', text)

    def test_scheduled_backtest_report_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            json_path = Path(tmpdir) / "scheduled.json"
            md_path = Path(tmpdir) / "scheduled.md"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            repo.upsert_token(
                "0xtoken",
                "MEME",
                "Meme",
                observed_at,
                {"is_binance_alpha": True, "alpha_score": 100},
            )
            signal_id = repo.insert_signal(
                "0xpair",
                "0xtoken",
                SignalDecision(
                    observed_at=observed_at,
                    strategy_version="v1",
                    score=66,
                    pair_state="focused",
                    should_alert=False,
                    reasons=("h1_volume_support",),
                    risk_flags=(),
                    features={
                        "price_usd": 1.0,
                        "volume_to_liquidity_h1": 1.2,
                        "h1_return_live": 0.18,
                        "h24_return_live": 0.4,
                    },
                ),
            )
            repo.upsert_signal_prediction(
                signal_id,
                pair_address="0xpair",
                token_address="0xtoken",
                observed_at=observed_at,
                prediction=PredictionResult(
                    predictor_version="p-test",
                    prob_2h_up20=0.08,
                    prob_6h_up50=0.04,
                    prob_24h_up100=0.05,
                    risk_6h_dd30=0.02,
                    opportunity_score=55,
                    short_momentum_score=55,
                    continuation_score=40,
                    breakout_score=30,
                    stage="acceleration",
                    reasons=("prediction_price_accelerating",),
                ),
            )
            repo.upsert_prediction_outcome(
                signal_id,
                {
                    "outcome_source": "test",
                    "base_price_source": "test",
                    "base_price_usd": 1.0,
                    "gecko_base_close_usd": 1.0,
                    "price_divergence_pct": 0.0,
                    "quality_flags": [],
                    "max_return_2h": 0.25,
                    "max_return_6h": 0.25,
                    "max_return_24h": 0.25,
                    "min_return_6h": -0.01,
                    "hit_2h_up20": 1,
                    "hit_6h_up50": 0,
                    "hit_24h_up100": 0,
                    "hit_6h_dd30": 0,
                    "sample_count_2h": 2,
                    "sample_count_6h": 6,
                    "sample_count_24h": 24,
                },
                evaluated_at=observed_at + timedelta(hours=24),
            )
            repo.close()

            output = io.StringIO()
            with patch.dict(os.environ, {"MONITOR_DATABASE_PATH": str(database_path)}, clear=True):
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "--env-file",
                            "",
                            "scheduled-backtest-report",
                            "--json-out",
                            str(json_path),
                            "--md-out",
                            str(md_path),
                            "--skip-refresh-outcomes",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("scheduled backtest report completed", output.getvalue())
            self.assertIn("Scheduled Backtest Report", md_path.read_text(encoding="utf-8"))

            repo = MonitorRepository(str(database_path))
            repo.initialize()
            state = repo.get_external_json_cache("runtime:scheduled_backtest:last_run")
            repo.close()
            self.assertIsNotNone(state)
            self.assertEqual(state["value"]["status"], "success")
            self.assertEqual(state["value"]["summary"]["total_rows"], 1)

    def test_strategy_feedback_report_writes_outputs_and_persists_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            json_path = Path(tmpdir) / "feedback.json"
            md_path = Path(tmpdir) / "feedback.md"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            for index, hit in enumerate((1, 0)):
                signal_id = repo.insert_signal(
                    f"0xpair{index}",
                    f"0xtoken{index}",
                    SignalDecision(
                        observed_at=observed_at + timedelta(hours=index * 3),
                        strategy_version="v1",
                        score=70,
                        pair_state="focused",
                        should_alert=False,
                        reasons=("h1_volume_support",),
                        risk_flags=(),
                        features={"price_usd": 1.0, "market_cap_bucket": "micro", "liquidity_usd": 80_000},
                    ),
                )
                repo.upsert_signal_prediction(
                    signal_id,
                    pair_address=f"0xpair{index}",
                    token_address=f"0xtoken{index}",
                    observed_at=observed_at + timedelta(hours=index * 3),
                    prediction=PredictionResult(
                        predictor_version="p-test",
                        prob_2h_up20=0.5,
                        prob_6h_up50=0.2,
                        prob_24h_up100=0.1,
                        risk_6h_dd30=0.1,
                        opportunity_score=70,
                        short_momentum_score=70,
                        continuation_score=40,
                        breakout_score=20,
                        stage="early" if hit else "exhaustion",
                        reasons=("prediction_price_accelerating",),
                    ),
                )
                repo.upsert_prediction_outcome(
                    signal_id,
                    {
                        "outcome_source": "test",
                        "base_price_source": "test",
                        "base_price_usd": 1.0,
                        "gecko_base_close_usd": 1.0,
                        "price_divergence_pct": 0.0,
                        "quality_flags": [],
                        "max_return_2h": 0.25 if hit else 0.0,
                        "max_return_6h": 0.25 if hit else 0.0,
                        "max_return_24h": 0.25 if hit else 0.0,
                        "min_return_6h": -0.01,
                        "hit_2h_up20": hit,
                        "hit_6h_up50": 0,
                        "hit_24h_up100": 0,
                        "hit_6h_dd30": 0,
                        "sample_count_2h": 2,
                        "sample_count_6h": 6,
                        "sample_count_24h": 24,
                    },
                    evaluated_at=observed_at + timedelta(hours=24),
                )
            repo.close()

            output = io.StringIO()
            with patch.dict(os.environ, {"MONITOR_DATABASE_PATH": str(database_path)}, clear=True):
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "--env-file",
                            "",
                            "strategy-feedback-report",
                            "--json-out",
                            str(json_path),
                            "--md-out",
                            str(md_path),
                            "--min-slice-events",
                            "1",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("strategy feedback report completed", output.getvalue())
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            latest = repo.get_latest_strategy_feedback_report()
            repo.close()
            self.assertIsNotNone(latest)
            self.assertEqual(latest["summary"]["prediction_count"], 2)

    def test_refresh_risk_enrichment_fixture_writes_observation_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            fixture_path = Path(tmpdir) / "risk.json"
            fixture_path.write_text(json_dumps({"0xtoken": {"risk_level": "high", "confidence": 0.7}}), encoding="utf-8")
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            repo.upsert_token("0xtoken", "MEME", "Meme", observed_at, {"is_binance_alpha": True})
            repo.close()

            output = io.StringIO()
            with patch.dict(os.environ, {"MONITOR_DATABASE_PATH": str(database_path)}, clear=True):
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "--env-file",
                            "",
                            "refresh-risk-enrichment",
                            "--fixture-json",
                            str(fixture_path),
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertIn("risk enrichment refreshed", output.getvalue())
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            latest = repo.get_latest_risk_snapshot("0xtoken", provider="fixture")
            repo.close()
            self.assertIsNotNone(latest)
            self.assertEqual(latest["risk_level"], "high")

    def test_lifecycle_inventory_outputs_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            repo.close()

            output = io.StringIO()
            with patch.dict(os.environ, {"MONITOR_DATABASE_PATH": str(database_path)}, clear=True):
                with redirect_stdout(output):
                    exit_code = main(["--env-file", "", "lifecycle-inventory", "--json"])

            self.assertEqual(exit_code, 0)
            self.assertIn('"tables"', output.getvalue())
            self.assertIn('"retention_candidates"', output.getvalue())

    def test_retention_plan_apply_requires_backup_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            repo.close()

            with patch.dict(os.environ, {"MONITOR_DATABASE_PATH": str(database_path)}, clear=True):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        main(["--env-file", "", "retention-plan", "--apply"])

            self.assertEqual(ctx.exception.code, 2)

    def test_scheduled_backtest_worker_once_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            json_path = Path(tmpdir) / "worker.json"
            md_path = Path(tmpdir) / "worker.md"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            repo.upsert_token("0xtoken", "MEME", "Meme", observed_at, {"is_binance_alpha": True})
            signal_id = repo.insert_signal(
                "0xpair",
                "0xtoken",
                SignalDecision(
                    observed_at=observed_at,
                    strategy_version="v1",
                    score=66,
                    pair_state="focused",
                    should_alert=False,
                    reasons=("h1_volume_support",),
                    risk_flags=(),
                    features={"price_usd": 1.0, "h1_return_live": 0.18},
                ),
            )
            repo.upsert_signal_prediction(
                signal_id,
                pair_address="0xpair",
                token_address="0xtoken",
                observed_at=observed_at,
                prediction=PredictionResult(
                    predictor_version="p-test",
                    prob_2h_up20=0.08,
                    prob_6h_up50=0.04,
                    prob_24h_up100=0.05,
                    risk_6h_dd30=0.02,
                    opportunity_score=55,
                    short_momentum_score=55,
                    continuation_score=40,
                    breakout_score=30,
                    stage="acceleration",
                    reasons=("prediction_price_accelerating",),
                ),
            )
            repo.upsert_prediction_outcome(
                signal_id,
                {
                    "outcome_source": "test",
                    "base_price_source": "test",
                    "base_price_usd": 1.0,
                    "gecko_base_close_usd": 1.0,
                    "price_divergence_pct": 0.0,
                    "quality_flags": [],
                    "max_return_2h": 0.25,
                    "max_return_6h": 0.25,
                    "max_return_24h": 0.25,
                    "min_return_6h": -0.01,
                    "hit_2h_up20": 1,
                    "hit_6h_up50": 0,
                    "hit_24h_up100": 0,
                    "hit_6h_dd30": 0,
                    "sample_count_2h": 2,
                    "sample_count_6h": 6,
                    "sample_count_24h": 24,
                },
                evaluated_at=observed_at + timedelta(hours=24),
            )
            repo.close()

            output = io.StringIO()
            with patch.dict(os.environ, {"MONITOR_DATABASE_PATH": str(database_path)}, clear=True):
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "--env-file",
                            "",
                            "run-scheduled-backtest-worker",
                            "--once",
                            "--json-out",
                            str(json_path),
                            "--md-out",
                            str(md_path),
                            "--skip-refresh-outcomes",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("scheduled backtest worker cycle completed", output.getvalue())

    def test_compact_history_dry_run_outputs_estimate_without_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            observed_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
            repo.insert_snapshot(
                PairSnapshot(
                    pair_address="0xpair",
                    token_address="0xtoken",
                    token_symbol="MEME",
                    token_name="Meme",
                    quote_token_address="0xquote",
                    quote_symbol="WBNB",
                    observed_at=observed_at,
                    pair_created_at=observed_at - timedelta(days=1),
                    dex_id="pancakeswap",
                    pair_url="https://example.com/pair",
                    price_usd=1.0,
                    price_native=0.01,
                    liquidity_usd=10_000,
                    fdv=100_000,
                    market_cap=90_000,
                    volume_m5=100,
                    volume_h1=1_000,
                    volume_h24=10_000,
                    buys_m5=3,
                    sells_m5=1,
                    buys_h1=20,
                    sells_h1=5,
                    price_change_m5=1,
                    price_change_h1=5,
                    price_change_h24=10,
                    website_count=1,
                    social_count=1,
                    boosts_active=0,
                    raw_payload={"large": "x" * 1000},
                ),
                age_minutes=60,
                risk_flags=[],
            )
            repo.insert_signal(
                "0xpair",
                "0xtoken",
                SignalDecision(
                    observed_at=observed_at,
                    strategy_version="v1",
                    score=55,
                    pair_state="watching",
                    should_alert=False,
                    reasons=("h1_volume_support",),
                    risk_flags=(),
                    features={"price_usd": 1.0, "experimental_metric": 123},
                ),
            )
            repo.close()

            output = io.StringIO()
            with patch.dict(os.environ, {"MONITOR_DATABASE_PATH": str(database_path)}, clear=True):
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "--env-file",
                            "",
                            "compact-history",
                            "--before",
                            "2026-04-02T00:00:00+00:00",
                            "--dry-run",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            text = output.getvalue()
            self.assertIn("mode=dry-run", text)
            self.assertIn("snapshots=1", text)
            self.assertIn("signals=1", text)
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            raw_json = repo._conn.execute("SELECT raw_json FROM snapshots WHERE pair_address = ?", ("0xpair",)).fetchone()["raw_json"]
            self.assertIn("large", raw_json)
            repo.close()


if __name__ == "__main__":
    unittest.main()
