from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.risk_enrichment import (
    FixtureRiskProvider,
    build_risk_failure_snapshot,
    normalize_risk_payload,
    refresh_risk_snapshots,
)
from token_meme_monitor.utils import json_dumps


class RiskEnrichmentTests(unittest.TestCase):
    def test_normalize_risk_payload_preserves_source_and_normalized_fields(self) -> None:
        fetched_at = datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc)

        snapshot = normalize_risk_payload(
            provider="fixture",
            token_address="0xtoken",
            payload={
                "risk_level": "high",
                "confidence": 0.8,
                "holder_concentration_pct": 0.62,
                "liquidity_locked": False,
                "owner_renounced": False,
                "buy_tax_pct": 0.03,
                "sell_tax_pct": 0.12,
            },
            fetched_at=fetched_at,
            ttl_hours=6,
        )

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["risk_level"], "high")
        self.assertEqual(snapshot["confidence"], 0.8)
        self.assertEqual(snapshot["normalized"]["holder_concentration_pct"], 0.62)
        self.assertEqual(snapshot["expires_at"], "2026-04-29T16:00:00+00:00")

    def test_failure_snapshot_records_reason_without_low_risk_default(self) -> None:
        fetched_at = datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc)

        snapshot = build_risk_failure_snapshot(
            provider="fixture",
            token_address="0xtoken",
            failure_reason="no_coverage",
            fetched_at=fetched_at,
            ttl_hours=1,
        )

        self.assertEqual(snapshot["status"], "failure")
        self.assertEqual(snapshot["risk_level"], "unknown")
        self.assertEqual(snapshot["failure_reason"], "no_coverage")

    def test_risk_snapshot_is_persisted_and_latest_can_be_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            snapshot = normalize_risk_payload(
                provider="fixture",
                token_address="0xtoken",
                payload={"risk_level": "low", "confidence": 0.9},
                fetched_at=datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc),
                ttl_hours=6,
            )

            snapshot_id = repo.insert_risk_snapshot(snapshot)
            latest = repo.get_latest_risk_snapshot("0xtoken", provider="fixture")
            repo.close()

            self.assertGreater(snapshot_id, 0)
            self.assertIsNotNone(latest)
            self.assertEqual(latest["risk_level"], "low")
            self.assertEqual(latest["status"], "ok")

    def test_refresh_risk_snapshots_respects_ttl_and_records_provider_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            now = datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc)
            current = normalize_risk_payload(
                provider="fixture",
                token_address="0xfresh",
                payload={"risk_level": "low"},
                fetched_at=now,
                ttl_hours=6,
            )
            repo.insert_risk_snapshot(current)
            provider = FixtureRiskProvider(
                {
                    "0xfresh": {"risk_level": "high"},
                    "0xfail": {"_error": "provider unavailable"},
                    "0xnew": {"risk_level": "medium", "confidence": 0.5},
                }
            )

            result = refresh_risk_snapshots(
                repo,
                ["0xfresh", "0xfail", "0xnew"],
                providers=[provider],
                now=now + timedelta(hours=1),
                ttl_hours=6,
            )
            fresh = repo.get_latest_risk_snapshot("0xfresh", provider="fixture")
            failed = repo.get_latest_risk_snapshot("0xfail", provider="fixture")
            new = repo.get_latest_risk_snapshot("0xnew", provider="fixture")
            repo.close()

            self.assertEqual(result["skipped_current"], 1)
            self.assertEqual(result["updated"], 1)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(fresh["risk_level"], "low")
            self.assertEqual(failed["status"], "failure")
            self.assertEqual(new["risk_level"], "medium")


if __name__ == "__main__":
    unittest.main()
