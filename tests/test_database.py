from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.models import PairSnapshot, PredictionResult, SignalDecision


class DatabaseTests(unittest.TestCase):
    def test_history_compaction_archives_payloads_and_restores_prediction_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            base = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
            cutoff = datetime(2026, 4, 2, 0, 0, tzinfo=timezone.utc)
            for offset, price in enumerate((1.0, 1.5, 1.2)):
                repo.insert_snapshot(
                    PairSnapshot(
                        pair_address="0xpair",
                        token_address="0xtoken",
                        token_symbol="MEME",
                        token_name="Meme",
                        quote_token_address="0xquote",
                        quote_symbol="WBNB",
                        observed_at=base + timedelta(minutes=offset * 10),
                        pair_created_at=base - timedelta(days=1),
                        dex_id="pancakeswap",
                        pair_url="https://example.com/pair",
                        price_usd=price,
                        price_native=price / 100,
                        liquidity_usd=10_000 + offset,
                        fdv=100_000,
                        market_cap=90_000,
                        volume_m5=100 + offset,
                        volume_h1=1_000 + offset,
                        volume_h24=10_000 + offset,
                        buys_m5=3 + offset,
                        sells_m5=1,
                        buys_h1=20,
                        sells_h1=5,
                        price_change_m5=1,
                        price_change_h1=5,
                        price_change_h24=10,
                        website_count=1,
                        social_count=1,
                        boosts_active=0,
                        raw_payload={"large": "x" * 1000, "price": price},
                    ),
                    age_minutes=60,
                    risk_flags=[],
                )
            signal_id = repo.insert_signal(
                "0xpair",
                "0xtoken",
                SignalDecision(
                    observed_at=base,
                    strategy_version="v1",
                    score=55,
                    pair_state="watching",
                    should_alert=False,
                    reasons=("h1_volume_support",),
                    risk_flags=(),
                    features={
                        "price_usd": 1.0,
                        "volume_h1": 1000,
                        "buy_sell_ratio_h1": 4.0,
                        "h1_return_live": 0.08,
                        "experimental_metric": 123,
                    },
                ),
            )
            repo.upsert_signal_prediction(
                signal_id,
                pair_address="0xpair",
                token_address="0xtoken",
                observed_at=base,
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

            summary = repo.compact_history(cutoff, batch_size=10)
            followup_estimate = repo.estimate_history_compaction(cutoff)

            self.assertEqual(summary["snapshot_rows_compacted"], 3)
            self.assertEqual(summary["signal_rows_compacted"], 1)
            self.assertEqual(followup_estimate["snapshot_rows"], 0)
            self.assertEqual(followup_estimate["signal_rows"], 0)
            self.assertEqual(followup_estimate["snapshot_hourly_rollup_rows"], 0)
            snapshot_row = repo._conn.execute("SELECT raw_json FROM snapshots WHERE pair_address = ? LIMIT 1", ("0xpair",)).fetchone()
            self.assertEqual(snapshot_row["raw_json"], "{}")
            self.assertEqual(
                repo._conn.execute("SELECT COUNT(*) AS n FROM snapshot_raw_archives").fetchone()["n"],
                3,
            )
            rollup = repo._conn.execute(
                "SELECT sample_count, open_price_usd, high_price_usd, low_price_usd, close_price_usd FROM snapshot_hourly_rollups"
            ).fetchone()
            self.assertEqual(rollup["sample_count"], 3)
            self.assertAlmostEqual(rollup["open_price_usd"], 1.0)
            self.assertAlmostEqual(rollup["high_price_usd"], 1.5)
            self.assertAlmostEqual(rollup["low_price_usd"], 1.0)
            self.assertAlmostEqual(rollup["close_price_usd"], 1.2)
            stored_signal = repo.get_signal_row(signal_id)
            self.assertNotIn("experimental_metric", stored_signal["feature_json"])
            self.assertNotIn("buy_sell_ratio_h1", stored_signal["feature_json"])
            self.assertIn("price_usd", stored_signal["feature_json"])
            self.assertIn("_history_compacted", stored_signal["feature_json"])
            dataset_rows = repo.list_prediction_dataset_rows()
            restored_features = dataset_rows[0]["feature_json"]
            self.assertIn('"experimental_metric":123', restored_features)
            self.assertIn('"buy_sell_ratio_h1":4.0', restored_features)
            repo._conn.execute(
                "UPDATE signals SET feature_json = ? WHERE id = ?",
                ('{"price_usd":2.0,"repair_metric":9}', signal_id),
            )
            repo._conn.commit()
            repaired_dataset_rows = repo.list_prediction_dataset_rows()
            repaired_features = repaired_dataset_rows[0]["feature_json"]
            self.assertIn('"repair_metric":9', repaired_features)
            self.assertIn('"price_usd":2.0', repaired_features)
            self.assertNotIn('"experimental_metric":123', repaired_features)
            repo._conn.execute(
                "UPDATE signals SET feature_json = ? WHERE id = ?",
                ('{"_history_compacted":true,"buy_sell_ratio_h1":4.0,"price_usd":1.0}', signal_id),
            )
            repo._conn.commit()
            recompact_summary = repo.compact_history(cutoff, batch_size=10)
            self.assertEqual(recompact_summary["signal_rows_compacted"], 0)
            self.assertEqual(recompact_summary["signal_rows_recompacted"], 1)
            recompacted_signal = repo.get_signal_row(signal_id)
            self.assertNotIn("buy_sell_ratio_h1", recompacted_signal["feature_json"])
            self.assertIn("price_usd", recompacted_signal["feature_json"])
            repo.close()

    def test_history_compaction_estimate_does_not_mutate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            observed_at = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
            cutoff = datetime(2026, 4, 2, 0, 0, tzinfo=timezone.utc)
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

            summary = repo.estimate_history_compaction(cutoff)

            self.assertEqual(summary["snapshot_rows"], 1)
            self.assertEqual(summary["signal_rows"], 1)
            self.assertGreater(summary["snapshot_raw_json_bytes"], 100)
            snapshot_row = repo._conn.execute("SELECT raw_json FROM snapshots WHERE pair_address = ?", ("0xpair",)).fetchone()
            self.assertIn("large", snapshot_row["raw_json"])
            self.assertEqual(
                repo._conn.execute("SELECT COUNT(*) AS n FROM snapshot_raw_archives").fetchone()["n"],
                0,
            )
            repo.close()

    def test_history_compaction_schema_has_global_observed_at_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()

            snapshot_indexes = {
                row["name"]
                for row in repo._conn.execute("PRAGMA index_list(snapshots)").fetchall()
            }
            signal_indexes = {
                row["name"]
                for row in repo._conn.execute("PRAGMA index_list(signals)").fetchall()
            }

            self.assertIn("idx_snapshots_observed_at", snapshot_indexes)
            self.assertIn("idx_signals_observed_at", signal_indexes)
            repo.close()

    def test_seed_pair_update_preserves_archived_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            kwargs = {
                "pair_address": "0xpair",
                "chain_id": "bsc",
                "token_address": "0xtoken",
                "token_symbol": "MEME",
                "token_name": "Meme",
                "quote_token_address": "0xquote",
                "quote_symbol": "WBNB",
                "token0_address": "0xtoken",
                "token1_address": "0xquote",
                "pair_created_at": now - timedelta(hours=1),
                "discovered_at": now,
                "metadata": {"seed_source": "binance_alpha"},
            }
            repo.upsert_seed_pair(**kwargs)
            repo.update_pair_after_snapshot(
                "0xpair",
                state="archived",
                dex_id="pancakeswap",
                token_symbol="MEME",
                token_name="Meme",
                last_snapshot_at=now,
                next_refresh_at=None,
                risk_flags=["liquidity_near_zero"],
                metadata={"pair_url": "https://example.com"},
                active=False,
            )
            repo.upsert_seed_pair(**{**kwargs, "token_name": "Meme Updated", "discovered_at": now + timedelta(minutes=5)})
            row = repo._conn.execute("SELECT state, active, next_refresh_at, token_name FROM pairs WHERE pair_address = ?", ("0xpair",)).fetchone()
            self.assertEqual(row["state"], "archived")
            self.assertEqual(row["active"], 0)
            self.assertIsNone(row["next_refresh_at"])
            self.assertEqual(row["token_name"], "Meme Updated")
            repo.close()

    def test_external_trend_metrics_are_cached_by_pair_and_observed_hour(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            observed_hour = "2026-04-25T12:00:00+00:00"
            fetched_at = datetime(2026, 4, 25, 13, 0, tzinfo=timezone.utc)

            repo.upsert_external_trend_metrics(
                "0xpair",
                observed_hour,
                external_return_2h=0.12,
                external_return_24h=None,
                fetched_at=fetched_at,
                raw_payload={"external_return_2h": 0.12, "external_return_24h": None},
            )
            row = repo.get_external_trend_metrics("0xpair", observed_hour)

            self.assertIsNotNone(row)
            self.assertEqual(row["pair_address"], "0xpair")
            self.assertEqual(row["observed_at_hour"], observed_hour)
            self.assertEqual(row["source"], "geckoterminal")
            self.assertAlmostEqual(row["external_return_2h"], 0.12)
            self.assertIsNone(row["external_return_24h"])

            repo.upsert_external_trend_metrics(
                "0xpair",
                observed_hour,
                external_return_2h=0.2,
                external_return_24h=0.5,
                fetched_at=fetched_at + timedelta(minutes=5),
                raw_payload={"external_return_2h": 0.2, "external_return_24h": 0.5},
            )
            updated = repo.get_external_trend_metrics("0xpair", observed_hour)

            self.assertIsNotNone(updated)
            self.assertAlmostEqual(updated["external_return_2h"], 0.2)
            self.assertAlmostEqual(updated["external_return_24h"], 0.5)
            repo.upsert_external_trend_metrics(
                "0xpair",
                "2026-04-25T13:00:00+00:00",
                external_return_2h=0.3,
                external_return_24h=0.7,
                fetched_at=fetched_at + timedelta(hours=1),
                raw_payload={"external_return_2h": 0.3, "external_return_24h": 0.7},
            )
            batch_rows = repo.list_external_trend_metrics(
                "0xpair",
                [
                    "2026-04-25T13:00:00+00:00",
                    observed_hour,
                    observed_hour,
                    "2026-04-25T14:00:00+00:00",
                ],
            )
            self.assertEqual(
                {row["observed_at_hour"] for row in batch_rows},
                {observed_hour, "2026-04-25T13:00:00+00:00"},
            )
            repo.close()

    def test_external_ohlcv_rows_and_fetch_windows_are_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            rows = [
                {"ts": 1_000, "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 100.0},
                {"ts": 2_000, "open": 1.1, "high": 1.4, "low": 1.0, "close": 1.3, "volume": 200.0},
            ]

            repo.upsert_external_ohlcv(
                network="bsc",
                pool_address="0xpool",
                timeframe="hour",
                aggregate=1,
                rows=rows,
            )
            repo.record_external_ohlcv_fetch(
                network="bsc",
                pool_address="0xpool",
                timeframe="hour",
                aggregate=1,
                limit=48,
                before_timestamp=3_000,
                row_count=2,
            )

            cached_rows = repo.list_external_ohlcv(
                network="bsc",
                pool_address="0xpool",
                timeframe="hour",
                aggregate=1,
                before_timestamp=3_000,
                limit=48,
            )
            row_count = repo.get_external_ohlcv_fetch_row_count(
                network="bsc",
                pool_address="0xpool",
                timeframe="hour",
                aggregate=1,
                limit=48,
                before_timestamp=3_000,
            )

            self.assertEqual([row["ts"] for row in cached_rows], [1_000, 2_000])
            self.assertAlmostEqual(cached_rows[-1]["close"], 1.3)
            self.assertEqual(row_count, 2)
            repo.close()

    def test_external_json_cache_round_trips_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            fetched_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

            repo.upsert_external_json_cache(
                "binance_futures_registry",
                {"MEME": {"usdm": ["MEMEUSDT"], "coinm": []}},
                fetched_at=fetched_at,
            )
            row = repo.get_external_json_cache("binance_futures_registry")

            self.assertIsNotNone(row)
            self.assertEqual(row["fetched_at"], "2026-04-25T12:00:00+00:00")
            self.assertEqual(row["value"]["MEME"]["usdm"], ["MEMEUSDT"])
            repo.close()

    def test_insert_signal_updates_existing_signal_for_same_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

            first_id = repo.insert_signal(
                "0xpair",
                "0xtoken",
                SignalDecision(
                    observed_at=observed_at,
                    strategy_version="v1",
                    score=20,
                    pair_state="watching",
                    should_alert=False,
                    reasons=("thin_m5_activity",),
                    risk_flags=("low_liquidity",),
                    features={"price_usd": 1.0},
                ),
            )
            second_id = repo.insert_signal(
                "0xpair",
                "0xtoken",
                SignalDecision(
                    observed_at=observed_at,
                    strategy_version="v1",
                    score=88,
                    pair_state="alerted",
                    should_alert=True,
                    reasons=("h1_volume_support",),
                    risk_flags=(),
                    features={"price_usd": 2.0},
                ),
            )
            row = repo.get_signal_row(first_id)

            self.assertEqual(first_id, second_id)
            self.assertIsNotNone(row)
            self.assertEqual(row["score"], 88)
            self.assertEqual(row["pair_state"], "alerted")
            self.assertEqual(row["should_alert"], 1)
            self.assertIn('"price_usd":2.0', row["feature_json"])
            repo.close()

    def test_prediction_and_outcome_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            signal_id = repo.insert_signal(
                "0xpair",
                "0xtoken",
                SignalDecision(
                    observed_at=observed_at,
                    strategy_version="v1",
                    score=65,
                    pair_state="focused",
                    should_alert=False,
                    reasons=("h1_volume_support",),
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
                    predictor_version="p1",
                    prob_2h_up20=0.25,
                    prob_6h_up50=0.18,
                    prob_24h_up100=0.09,
                    risk_6h_dd30=0.12,
                    opportunity_score=68,
                    short_momentum_score=68,
                    continuation_score=47,
                    breakout_score=22,
                    stage="acceleration",
                    reasons=("prediction_alpha_hot",),
                ),
            )
            repo.upsert_prediction_outcome(
                signal_id,
                {
                    "max_return_2h": 0.22,
                    "max_return_6h": 0.51,
                    "max_return_24h": 1.2,
                    "min_return_6h": -0.05,
                    "hit_2h_up20": 1,
                    "hit_6h_up50": 1,
                    "hit_24h_up100": 1,
                    "hit_6h_dd30": 0,
                    "sample_count_2h": 4,
                    "sample_count_6h": 8,
                    "sample_count_24h": 20,
                },
                evaluated_at=observed_at + timedelta(days=1),
            )

            rows = repo.list_recent_signals("0xpair")

            self.assertEqual(rows[0]["prediction_opportunity_score"], 68)
            self.assertEqual(rows[0]["prediction_short_momentum_score"], 68)
            self.assertEqual(rows[0]["prediction_continuation_score"], 47)
            self.assertEqual(rows[0]["prediction_breakout_score"], 22)
            self.assertEqual(rows[0]["prediction_stage"], "acceleration")
            self.assertEqual(rows[0]["outcome_hit_24h_up100"], 1)
            repo.close()

    def test_due_pairs_can_be_filtered_by_token_before_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            for pair_address, token_address, state in [
                ("0xnonalpha", "0xnonalphatoken", "alerted"),
                ("0xalpha", "0xalphatoken", "watching"),
            ]:
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
                    pair_created_at=now - timedelta(hours=1),
                    discovered_at=now - timedelta(minutes=5),
                    metadata={},
                )
                repo.update_pair_after_snapshot(
                    pair_address,
                    state=state,
                    dex_id="pancakeswap",
                    token_symbol="MEME",
                    token_name="Meme",
                    last_snapshot_at=now - timedelta(minutes=2),
                    next_refresh_at=now - timedelta(minutes=1),
                    risk_flags=[],
                    metadata={},
                    active=True,
                )
            rows = repo.list_due_pairs_for_tokens(["0xalphatoken"], now, limit=1)
            self.assertEqual([row["pair_address"] for row in rows], ["0xalpha"])
            repo.close()

    def test_alpha_tokens_need_seed_only_when_missing_or_ttl_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            fresh_seeded_at = (now - timedelta(minutes=5)).isoformat()
            stale_seeded_at = (now - timedelta(minutes=30)).isoformat()
            repo.upsert_token("0xfresh", "FRESH", "Fresh", now, {"pair_seeded_at": fresh_seeded_at})
            repo.upsert_token("0xstale", "STALE", "Stale", now, {"pair_seeded_at": stale_seeded_at})
            repo.upsert_token("0xmissing", "MISS", "Missing", now, {})
            for pair_address, token_address in [("0xfreshpair", "0xfresh"), ("0xstalepair", "0xstale")]:
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
                    pair_created_at=now - timedelta(hours=1),
                    discovered_at=now,
                    metadata={},
                )
            due = repo.list_alpha_tokens_needing_pair_seed(
                ["0xfresh", "0xstale", "0xmissing"],
                now=now,
                refresh_after=now - timedelta(minutes=15),
                limit=10,
            )
            self.assertEqual(due, ["0xmissing", "0xstale"])
            repo.close()

    def test_recent_pair_seed_failure_does_not_consume_batch_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            fresh_failed_at = (now - timedelta(minutes=5)).isoformat()
            stale_failed_at = (now - timedelta(minutes=30)).isoformat()
            repo.upsert_token("0xaaa", "AAA", "AAA", now, {"pair_seed_failed_at": fresh_failed_at})
            repo.upsert_token("0xbbb", "BBB", "BBB", now, {"pair_seed_failed_at": stale_failed_at})
            repo.upsert_token("0xzzz", "ZZZ", "ZZZ", now, {})

            due = repo.list_alpha_tokens_needing_pair_seed(
                ["0xaaa", "0xbbb", "0xzzz"],
                now=now,
                refresh_after=now - timedelta(minutes=15),
                limit=2,
            )

            self.assertEqual(due, ["0xbbb", "0xzzz"])
            repo.close()

    def test_recent_holder_metric_attempt_does_not_consume_batch_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            token_rows = [
                ("0xrecent", now + timedelta(minutes=2), {"holder_metrics_attempted_at": (now - timedelta(minutes=5)).isoformat()}),
                ("0xstale", now + timedelta(minutes=1), {"holder_metrics_attempted_at": (now - timedelta(hours=25)).isoformat()}),
                ("0xmissing", now, {}),
            ]
            for index, (token_address, last_seen_at, metadata) in enumerate(token_rows):
                repo.upsert_token(token_address, token_address.upper(), token_address.upper(), last_seen_at, metadata)
                repo.upsert_seed_pair(
                    pair_address=f"0xpair{index}",
                    chain_id="bsc",
                    token_address=token_address,
                    token_symbol=token_address.upper(),
                    token_name=token_address.upper(),
                    quote_token_address="0xquote",
                    quote_symbol="WBNB",
                    token0_address=token_address,
                    token1_address="0xquote",
                    pair_created_at=now - timedelta(hours=1),
                    discovered_at=last_seen_at,
                    metadata={},
                )

            due = repo.list_tokens_needing_holder_metrics(
                stale_before=now - timedelta(hours=24),
                limit=2,
            )

            self.assertEqual([row["token_address"] for row in due], ["0xstale", "0xmissing"])
            repo.close()

    def test_pair_overview_includes_latest_signal_context_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            repo.upsert_token("0xtoken", "MEME", "Meme", now, {"is_binance_alpha": True})
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
                pair_created_at=now - timedelta(hours=1),
                discovered_at=now,
                metadata={},
            )
            repo.insert_signal(
                "0xpair",
                "0xtoken",
                SignalDecision(
                    observed_at=now,
                    strategy_version="v1",
                    score=81,
                    pair_state="focused",
                    should_alert=False,
                    reasons=("h1_volume_support",),
                    risk_flags=("sell_pressure",),
                    features={"price_usd": 1.23, "liquidity_usd": 45678},
                ),
            )

            row = repo.list_pair_overview(limit=1)[0]

            self.assertEqual(row["last_score"], 81)
            self.assertEqual(row["last_pair_state"], "focused")
            self.assertEqual(row["last_should_alert"], 0)
            self.assertIsNotNone(row["last_signal_at"])
            self.assertEqual(row["last_reasons"], '["h1_volume_support"]')
            self.assertEqual(row["last_risk_flags"], '["sell_pressure"]')
            self.assertIn('"liquidity_usd":45678', row["last_feature_json"])
            repo.close()

    def test_pair_overview_limit_prioritizes_recent_active_alpha_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = MonitorRepository(str(Path(tmpdir) / "monitor.db"))
            repo.initialize()
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

            rows = [
                (
                    "0xinactivealphapair",
                    "0xinactivealphatoken",
                    {"is_binance_alpha": True},
                    now + timedelta(minutes=2),
                    100,
                    False,
                ),
                ("0xnonalphapair", "0xnonalphatoken", {}, now + timedelta(minutes=1), 100, True),
                ("0xoldalphapair", "0xoldalphatoken", {"is_binance_alpha": True}, now - timedelta(hours=2), 99, True),
                ("0xrecentalphapair", "0xrecentalphatoken", {"is_binance_alpha": True}, now, 1, True),
            ]
            for pair_address, token_address, metadata, observed_at, score, active in rows:
                repo.upsert_token(token_address, "MEME", "Meme", now, metadata)
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
                    pair_created_at=now - timedelta(hours=1),
                    discovered_at=now,
                    metadata={},
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
                        pair_created_at=now - timedelta(hours=1),
                        dex_id="pancakeswap",
                        pair_url="https://example.com",
                        price_usd=1.0,
                        price_native=0.01,
                        liquidity_usd=100_000,
                        fdv=1_000_000,
                        market_cap=900_000,
                        volume_m5=1_000,
                        volume_h1=10_000,
                        volume_h24=100_000,
                        buys_m5=10,
                        sells_m5=2,
                        buys_h1=40,
                        sells_h1=20,
                        price_change_m5=1,
                        price_change_h1=5,
                        price_change_h24=20,
                        website_count=1,
                        social_count=1,
                        boosts_active=0,
                        raw_payload={},
                    ),
                    age_minutes=60,
                    risk_flags=[],
                )
                repo.update_pair_after_snapshot(
                    pair_address,
                    state="watching" if active else "archived",
                    dex_id="pancakeswap",
                    token_symbol="MEME",
                    token_name="Meme",
                    last_snapshot_at=observed_at,
                    next_refresh_at=observed_at + timedelta(minutes=1) if active else None,
                    risk_flags=[],
                    metadata={},
                    active=active,
                )
                repo.insert_signal(
                    pair_address,
                    token_address,
                    SignalDecision(
                        observed_at=observed_at,
                        strategy_version="v1",
                        score=score,
                        pair_state="watching",
                        should_alert=False,
                        reasons=(),
                        risk_flags=(),
                        features={"price_usd": 1.0},
                    ),
                )

            row = repo.list_pair_overview(limit=1)[0]

            self.assertEqual(row["pair_address"], "0xrecentalphapair")
            repo.close()


if __name__ == "__main__":
    unittest.main()
