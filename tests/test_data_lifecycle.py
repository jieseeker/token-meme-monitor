from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from token_meme_monitor.data_lifecycle import (
    build_lifecycle_integrity_report,
    build_lifecycle_inventory,
    build_retention_plan,
)
from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.models import PairSnapshot, PredictionResult, SignalDecision


class DataLifecycleTests(unittest.TestCase):
    def test_inventory_reports_rows_age_ranges_and_retention_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            old = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
            repo.insert_snapshot(_snapshot(old), age_minutes=60, risk_flags=[])

            inventory = build_lifecycle_inventory(
                repo,
                database_path=str(database_path),
                snapshot_retention_days=14,
                now=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(inventory["tables"]["snapshots"]["rows"], 1)
            self.assertEqual(inventory["tables"]["snapshots"]["oldest"], old.isoformat(timespec="seconds"))
            self.assertEqual(inventory["retention_candidates"]["snapshots"], 1)
            repo.close()

    def test_integrity_reports_archive_shadowing_full_repaired_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            observed_at = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
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
                    features={"price_usd": 1.0, "repaired": True},
                ),
            )
            repo._conn.execute(
                """
                INSERT INTO signal_feature_archives(signal_id, archived_at, compression, feature_json_z)
                VALUES(?, ?, 'zlib', ?)
                """,
                (signal_id, observed_at.isoformat(timespec="seconds"), b"x"),
            )
            repo._conn.commit()

            report = build_lifecycle_integrity_report(repo)

            finding = report["findings"]["archive_shadowed_by_full_signal"]
            self.assertEqual(finding["count"], 1)
            self.assertEqual(finding["severity"], "warn")
            repo.close()

    def test_retention_plan_is_dry_run_and_does_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "monitor.db"
            repo = MonitorRepository(str(database_path))
            repo.initialize()
            old = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
            repo.insert_snapshot(_snapshot(old), age_minutes=60, risk_flags=[])

            plan = build_retention_plan(
                repo,
                older_than_days=14,
                now=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
            )
            remaining = repo._conn.execute("SELECT count(*) FROM snapshots").fetchone()[0]

            self.assertEqual(plan["mode"], "dry-run")
            self.assertEqual(plan["actions"]["compact_snapshots"]["candidate_rows"], 1)
            self.assertEqual(remaining, 1)
            repo.close()


def _snapshot(observed_at: datetime) -> PairSnapshot:
    return PairSnapshot(
        pair_address="0xpair",
        token_address="0xtoken",
        token_symbol="MEME",
        token_name="Meme",
        quote_token_address="0xquote",
        quote_symbol="WBNB",
        observed_at=observed_at,
        pair_created_at=observed_at - timedelta(hours=1),
        dex_id="pancakeswap",
        pair_url="https://example.com",
        price_usd=1.0,
        price_native=0.01,
        liquidity_usd=50_000,
        fdv=500_000,
        market_cap=450_000,
        volume_m5=100,
        volume_h1=1_000,
        volume_h24=10_000,
        buys_m5=2,
        sells_m5=1,
        buys_h1=10,
        sells_h1=5,
        price_change_m5=1,
        price_change_h1=2,
        price_change_h24=3,
        website_count=1,
        social_count=1,
        boosts_active=0,
        raw_payload={"source": "test"},
    )


if __name__ == "__main__":
    unittest.main()
