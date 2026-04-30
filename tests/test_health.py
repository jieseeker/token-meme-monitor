from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.health import build_health_report, _pair_stats, _prediction_stats
from token_meme_monitor.models import PredictionResult, SignalDecision
from token_meme_monitor.scheduled_backtest import SCHEDULED_BACKTEST_STATE_CACHE_KEY
from token_meme_monitor.utils import isoformat_utc, utcnow


class HealthReportTests(unittest.TestCase):
    def test_pair_stats_counts_same_day_iso_active_pair_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            cutoff = datetime(2026, 4, 29, 13, 30, tzinfo=timezone.utc)
            _install_sqlite_time_fakes(repo, stale_pair_cutoff=cutoff)
            last_snapshot_at = cutoff - timedelta(minutes=1)
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
                pair_created_at=cutoff - timedelta(hours=2),
                discovered_at=cutoff - timedelta(hours=2),
                metadata={},
            )
            repo.update_pair_after_snapshot(
                "0xpair",
                state="watching",
                dex_id="pancakeswap",
                token_symbol="MEME",
                token_name="Meme",
                last_snapshot_at=last_snapshot_at,
                next_refresh_at=cutoff,
                risk_flags=[],
                metadata={},
            )

            stats = _pair_stats(repo)

            self.assertEqual(stats["stale_active_pairs"], 1)
            repo.close()

    def test_prediction_stats_counts_same_day_iso_prediction_as_mature_missing_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            cutoff = datetime(2026, 4, 29, 13, 0, tzinfo=timezone.utc)
            _install_sqlite_time_fakes(repo, mature_prediction_cutoff=cutoff)
            observed_at = cutoff - timedelta(minutes=1)
            signal_id = repo.insert_signal(
                "0xpair",
                "0xtoken",
                SignalDecision(
                    observed_at=observed_at,
                    strategy_version="v1",
                    score=55,
                    pair_state="watching",
                    should_alert=False,
                    reasons=("test",),
                    risk_flags=(),
                    features={"price_usd": 1.0},
                ),
            )
            repo.upsert_signal_prediction(
                signal_id,
                pair_address="0xpair",
                token_address="0xtoken",
                observed_at=observed_at,
                prediction=PredictionResult(
                    predictor_version="p-test",
                    prob_2h_up20=0.1,
                    prob_6h_up50=0.05,
                    prob_24h_up100=0.02,
                    risk_6h_dd30=0.03,
                    opportunity_score=40,
                    short_momentum_score=40,
                    continuation_score=20,
                    breakout_score=10,
                    stage="early",
                    reasons=("prediction_base",),
                ),
            )

            stats = _prediction_stats(repo)

            self.assertEqual(stats["mature_missing_outcomes"], 1)
            repo.close()

    def test_health_report_marks_clean_database_as_ok_when_scheduler_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            _record_scheduler_success(repo)

            report = build_health_report(repo, database_path=str(database_path))

            self.assertEqual(report["severity"]["status"], "ok")
            repo.close()

    def test_health_report_marks_missing_mature_outcomes_as_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            _record_scheduler_success(repo)
            observed_at = utcnow() - timedelta(hours=26)
            signal_id = repo.insert_signal(
                "0xpair",
                "0xtoken",
                SignalDecision(
                    observed_at=observed_at,
                    strategy_version="v1",
                    score=55,
                    pair_state="watching",
                    should_alert=False,
                    reasons=("test",),
                    risk_flags=(),
                    features={"price_usd": 1.0},
                ),
            )
            repo.upsert_signal_prediction(
                signal_id,
                pair_address="0xpair",
                token_address="0xtoken",
                observed_at=observed_at,
                prediction=PredictionResult(
                    predictor_version="p-test",
                    prob_2h_up20=0.1,
                    prob_6h_up50=0.05,
                    prob_24h_up100=0.02,
                    risk_6h_dd30=0.03,
                    opportunity_score=40,
                    short_momentum_score=40,
                    continuation_score=20,
                    breakout_score=10,
                    stage="early",
                    reasons=("prediction_base",),
                ),
            )

            report = build_health_report(repo, database_path=str(database_path))

            self.assertEqual(report["severity"]["status"], "warn")
            self.assertEqual(report["severity"]["checks"]["mature_missing_outcomes"]["status"], "warn")
            repo.close()

    def test_health_report_marks_scheduled_failure_as_critical(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            repo.upsert_external_json_cache(
                "runtime:scheduled_backtest:last_run",
                {
                    "name": "scheduled_backtest",
                    "status": "failure",
                    "started_at": "2026-04-29T10:00:00+00:00",
                    "finished_at": "2026-04-29T10:00:02+00:00",
                    "duration_seconds": 2.0,
                    "error": "RuntimeError: boom",
                },
            )

            report = build_health_report(repo, database_path=str(database_path))

            self.assertEqual(report["scheduled_jobs"]["scheduled_backtest"]["status"], "failure")
            self.assertEqual(report["severity"]["status"], "critical")
            self.assertEqual(report["severity"]["checks"]["scheduled_backtest"]["status"], "critical")
            repo.close()

    def test_health_report_includes_risk_enrichment_observation_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            _record_scheduler_success(repo)
            repo.insert_risk_snapshot(
                {
                    "provider": "fixture",
                    "token_address": "0xtoken",
                    "fetched_at": "2026-04-29T10:00:00+00:00",
                    "expires_at": "2026-04-29T16:00:00+00:00",
                    "status": "ok",
                    "risk_level": "high",
                    "confidence": 0.8,
                    "normalized": {},
                    "raw": {},
                    "failure_reason": None,
                }
            )

            report = build_health_report(repo, database_path=str(database_path))

            self.assertEqual(report["risk_enrichment"]["total"], 1)
            self.assertEqual(report["risk_enrichment"]["high_risk"], 1)
            repo.close()

    def test_health_report_includes_lifecycle_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            _record_scheduler_success(repo)

            report = build_health_report(repo, database_path=str(database_path))

            self.assertIn("lifecycle", report)
            self.assertIn("finding_count", report["lifecycle"])
            repo.close()


def _install_sqlite_time_fakes(
    repo: MonitorRepository,
    *,
    stale_pair_cutoff: datetime | None = None,
    mature_prediction_cutoff: datetime | None = None,
) -> None:
    def fake_datetime(*args: object) -> str | None:
        if args == ("now", "-30 minutes") and stale_pair_cutoff is not None:
            return stale_pair_cutoff.strftime("%Y-%m-%d %H:%M:%S")
        if args == ("now", "-25 hours") and mature_prediction_cutoff is not None:
            return mature_prediction_cutoff.strftime("%Y-%m-%d %H:%M:%S")
        return None

    def fake_unixepoch(*args: object) -> int | None:
        if args == ("now", "-30 minutes") and stale_pair_cutoff is not None:
            return int(stale_pair_cutoff.timestamp())
        if args == ("now", "-25 hours") and mature_prediction_cutoff is not None:
            return int(mature_prediction_cutoff.timestamp())
        if len(args) != 1 or args[0] is None:
            return None
        parsed = datetime.fromisoformat(str(args[0]))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.astimezone(timezone.utc).timestamp())

    repo._conn.create_function("datetime", -1, fake_datetime)
    repo._conn.create_function("unixepoch", -1, fake_unixepoch)


def _record_scheduler_success(repo: MonitorRepository) -> None:
    now = utcnow()
    repo.upsert_external_json_cache(
        SCHEDULED_BACKTEST_STATE_CACHE_KEY,
        {
            "name": "scheduled_backtest",
            "status": "success",
            "started_at": isoformat_utc(now - timedelta(seconds=2)),
            "finished_at": isoformat_utc(now),
            "duration_seconds": 2.0,
            "summary": {},
        },
        fetched_at=now,
    )
