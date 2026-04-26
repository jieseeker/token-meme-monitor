from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from token_meme_monitor.cli import main
from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.models import PairSnapshot, SignalDecision
from token_meme_monitor.predictions import PREDICTOR_VERSION


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


if __name__ == "__main__":
    unittest.main()
