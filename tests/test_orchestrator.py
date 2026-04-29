from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from token_meme_monitor.config import AppConfig, SignalConfig
from token_meme_monitor.models import AlphaToken, DiscoveredPair, PairSnapshot
from token_meme_monitor.orchestrator import MonitorWorker
from token_meme_monitor.utils import json_loads, parse_datetime


class FakeAlphaClient:
    def __init__(self, tokens: list[AlphaToken]) -> None:
        self.tokens = tokens

    def fetch_bsc_tokens(self) -> list[AlphaToken]:
        return self.tokens


class FakeBinanceMarketClient:
    def __init__(self, registry: dict | None = None, error: requests.RequestException | None = None) -> None:
        self.registry = registry or {}
        self.error = error
        self.calls = 0

    def fetch_futures_registry(self) -> dict:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.registry


class FakeAlphaRankClient:
    def __init__(self, metrics: dict[str, dict] | None = None) -> None:
        self.metrics = metrics or {}
        self.calls = 0

    def fetch_bsc_alpha_rank(self) -> dict[str, dict]:
        self.calls += 1
        return self.metrics


class FakeDexScreenerClient:
    def __init__(self, snapshot: PairSnapshot | None = None) -> None:
        self.calls: list[list[str]] = []
        self.snapshot = snapshot

    def fetch_best_pairs_for_tokens(self, *, chain_id: str, token_addresses: list[str], quote_tokens: dict) -> dict:
        self.calls.append(list(token_addresses))
        now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        return {
            address: {
                "pair_address": f"0xpair{index}",
                "token_symbol": "MEME",
                "token_name": "Meme",
                "quote_token_address": "0xquote",
                "quote_symbol": "WBNB",
                "token0_address": address,
                "token1_address": "0xquote",
                "pair_created_at": now - timedelta(hours=1),
                "pair_url": "https://example.com",
                "dex_id": "pancakeswap",
            }
            for index, address in enumerate(token_addresses)
        }

    def fetch_pair_snapshot(self, **kwargs) -> PairSnapshot | None:
        return self.snapshot


class FakeHoneypotClient:
    def __init__(self) -> None:
        self.holder_calls: list[str] = []
        self.top10_calls: list[str] = []

    def fetch_holder_count(self, token_address: str, chain_id: int = 56) -> int:
        self.holder_calls.append(token_address)
        return 1234

    def fetch_top10_holder_share(self, token_address: str, chain_id: int = 56, top_n: int = 10) -> float:
        self.top10_calls.append(token_address)
        return 0.42


class FailingHoneypotClient:
    def fetch_holder_count(self, token_address: str, chain_id: int = 56) -> int:
        raise AssertionError("holder lookup should not run during pair refresh")

    def fetch_top10_holder_share(self, token_address: str, chain_id: int = 56, top_n: int = 10) -> float:
        raise AssertionError("top holder lookup should not run during pair refresh")


class RecordingFailingDiscoveryClient:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    def latest_safe_block(self, confirmations: int) -> int:
        del confirmations
        self.calls += 1
        self.events.append("discovery")
        raise RuntimeError("rpc unavailable")

    def scan_pair_created_events(self, start_block: int, end_block: int) -> list:
        del start_block, end_block
        raise AssertionError("scan should not run when latest block fails")


class FailingLatestBlockDiscoveryClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def latest_safe_block(self, confirmations: int) -> int:
        del confirmations
        raise self.exc

    def scan_pair_created_events(self, start_block: int, end_block: int) -> list:
        del start_block, end_block
        raise AssertionError("scan should not run when latest block fails")


class EmptyDiscoveryClient:
    def latest_safe_block(self, confirmations: int) -> int:
        del confirmations
        return 0

    def scan_pair_created_events(self, start_block: int, end_block: int) -> list:
        del start_block, end_block
        return []


class PartiallyFailingDiscoveryClient:
    def latest_safe_block(self, confirmations: int) -> int:
        del confirmations
        return 2

    def scan_pair_created_events(self, start_block: int, end_block: int) -> list:
        if start_block == 1 and end_block == 1:
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            return [
                DiscoveredPair(
                    pair_address="0xpair",
                    token_address="0xtoken",
                    quote_token_address="0xquote",
                    quote_symbol="WBNB",
                    token0_address="0xtoken",
                    token1_address="0xquote",
                    pair_created_at=now - timedelta(hours=1),
                    discovered_at=now,
                    first_seen_block=1,
                    first_seen_tx_hash="0xtx",
                    first_seen_log_index=0,
                )
            ]
        raise RuntimeError("rpc unavailable after partial success")


class RecordingDexScreenerClient(FakeDexScreenerClient):
    def __init__(self, events: list[str], snapshot: PairSnapshot | None = None) -> None:
        super().__init__(snapshot=snapshot)
        self.events = events

    def fetch_pair_snapshot(self, **kwargs) -> PairSnapshot | None:
        self.events.append("snapshot")
        return super().fetch_pair_snapshot(**kwargs)


def alpha_token(address: str, symbol: str = "MEME", holders: int | None = 1000) -> AlphaToken:
    return AlphaToken(
        token_address=address,
        chain_id="56",
        chain_name="BSC",
        symbol=symbol,
        name=symbol.title(),
        price=1.0,
        market_cap=1_000_000,
        fdv=1_000_000,
        liquidity=100_000,
        volume_24h=500_000,
        holders=holders,
        alpha_id=symbol,
        alpha_score=80,
        listing_time=None,
        raw_payload={},
    )


class OrchestratorTests(unittest.TestCase):
    def test_missing_pair_created_at_is_scheduled_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                    signal=SignalConfig(base_poll_interval_seconds=60),
                )
            )
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            worker._repo.upsert_seed_pair(
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
            worker._refresh_pair(
                {
                    "pair_address": "0xpair",
                    "token_address": "0xtoken",
                    "quote_token_address": "0xquote",
                    "quote_symbol": "WBNB",
                    "pair_created_at": "",
                }
            )
            row = worker._repo._conn.execute(
                "SELECT next_refresh_at, metadata_json FROM pairs WHERE pair_address = ?",
                ("0xpair",),
            ).fetchone()
            metadata = json_loads(row["metadata_json"], {})
            self.assertEqual(metadata["last_retry_reason"], "missing_pair_created_at")
            self.assertIsNotNone(parse_datetime(row["next_refresh_at"]))
            worker.close()

    def test_unindexed_dexscreener_pair_uses_longer_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                    signal=SignalConfig(base_poll_interval_seconds=60),
                )
            )
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            worker._dexscreener = FakeDexScreenerClient(snapshot=None)
            worker._repo.upsert_seed_pair(
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

            worker._refresh_pair(
                {
                    "pair_address": "0xpair",
                    "token_address": "0xtoken",
                    "quote_token_address": "0xquote",
                    "quote_symbol": "WBNB",
                    "pair_created_at": (now - timedelta(hours=1)).isoformat(),
                }
            )

            row = worker._repo._conn.execute(
                "SELECT next_refresh_at, metadata_json FROM pairs WHERE pair_address = ?",
                ("0xpair",),
            ).fetchone()
            metadata = json_loads(row["metadata_json"], {})
            retry_at = parse_datetime(row["next_refresh_at"])
            self.assertEqual(metadata["snapshot_not_available_count"], 1)
            self.assertEqual(metadata["last_retry_reason"], "snapshot_not_available")
            self.assertIsNotNone(retry_at)
            self.assertGreaterEqual((retry_at - datetime.now(timezone.utc)).total_seconds(), 290)
            worker.close()

    def test_alpha_pair_seed_uses_ttl_after_initial_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                    binance_alpha_pair_seed_refresh_minutes=15,
                    binance_alpha_seed_batch_size=10,
                )
            )
            dex = FakeDexScreenerClient()
            worker._alpha_client = FakeAlphaClient([alpha_token("0xtoken1")])
            worker._binance_market_client = FakeBinanceMarketClient()
            worker._dexscreener = dex

            self.assertEqual(worker._sync_alpha_watchlist(), 1)
            self.assertEqual(dex.calls, [["0xtoken1"]])

            self.assertEqual(worker._sync_alpha_watchlist(), 0)
            self.assertEqual(dex.calls, [["0xtoken1"]])
            worker.close()

    def test_alpha_sync_does_not_clear_fallback_holder_count_when_alpha_omits_holders(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                    binance_alpha_seed_batch_size=10,
                )
            )
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            worker._repo.upsert_token(
                "0xtoken",
                "MEME",
                "Meme",
                now,
                {"holder_count": 1234, "holder_count_source": "honeypot"},
            )
            worker._alpha_client = FakeAlphaClient([alpha_token("0xtoken", holders=None)])
            worker._binance_market_client = FakeBinanceMarketClient()
            worker._dexscreener = FakeDexScreenerClient()

            worker._sync_alpha_watchlist()
            metadata = worker._repo.get_token_metadata("0xtoken")

            self.assertEqual(metadata["holder_count"], 1234)
            self.assertEqual(metadata["holder_count_source"], "honeypot")
            worker.close()

    def test_empty_alpha_refresh_keeps_previous_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                )
            )
            worker._alpha_client = FakeAlphaClient([alpha_token("0xtoken")])
            worker._binance_market_client = FakeBinanceMarketClient()
            self.assertTrue(worker._refresh_alpha_cache_if_needed())
            worker._alpha_client = FakeAlphaClient([])
            worker._alpha_refreshed_at = None

            self.assertTrue(worker._refresh_alpha_cache_if_needed())

            self.assertEqual(list(worker._alpha_token_map.keys()), ["0xtoken"])
            self.assertIsNotNone(worker._alpha_refreshed_at)
            worker.close()

    def test_initial_empty_alpha_refresh_blocks_alpha_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                )
            )
            worker._alpha_client = FakeAlphaClient([])
            worker._binance_market_client = FakeBinanceMarketClient()

            self.assertFalse(worker._refresh_alpha_cache_if_needed())
            self.assertEqual(worker._alpha_token_map, {})
            worker.close()

    def test_partial_alpha_refresh_keeps_previous_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                )
            )
            worker._alpha_client = FakeAlphaClient(
                [
                    alpha_token("0xtoken1", symbol="AAA"),
                    alpha_token("0xtoken2", symbol="BBB"),
                    alpha_token("0xtoken3", symbol="CCC"),
                    alpha_token("0xtoken4", symbol="DDD"),
                ]
            )
            worker._binance_market_client = FakeBinanceMarketClient()
            self.assertTrue(worker._refresh_alpha_cache_if_needed())
            worker._alpha_client = FakeAlphaClient([alpha_token("0xtoken1", symbol="AAA")])
            worker._alpha_refreshed_at = None

            self.assertTrue(worker._refresh_alpha_cache_if_needed())

            self.assertEqual(set(worker._alpha_token_map.keys()), {"0xtoken1", "0xtoken2", "0xtoken3", "0xtoken4"})
            worker.close()

    def test_binance_futures_registry_uses_fresh_sqlite_cache_without_requesting_binance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = str(Path(tmpdir) / "monitor.db")
            cached_at = datetime.now(timezone.utc)
            repo = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=database_path,
                )
            )._repo
            repo.upsert_external_json_cache(
                "binance_futures_registry",
                {"MEME": {"usdm": ["MEMEUSDT"], "coinm": []}},
                fetched_at=cached_at,
            )
            repo.close()

            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=database_path,
                )
            )
            market_client = FakeBinanceMarketClient(error=requests.HTTPError("should not fetch when cache is fresh"))
            worker._alpha_client = FakeAlphaClient([alpha_token("0xtoken")])
            worker._binance_market_client = market_client

            self.assertTrue(worker._refresh_alpha_cache_if_needed())

            self.assertEqual(market_client.calls, 0)
            self.assertEqual(worker._binance_futures_registry["MEME"]["usdm"], ["MEMEUSDT"])
            worker.close()

    def test_binance_futures_registry_rate_limit_sets_retry_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                )
            )
            response = requests.Response()
            response.status_code = 418
            market_client = FakeBinanceMarketClient(error=requests.HTTPError("limited", response=response))
            worker._alpha_client = FakeAlphaClient([alpha_token("0xtoken")])
            worker._binance_market_client = market_client

            self.assertTrue(worker._refresh_alpha_cache_if_needed())
            worker._alpha_refreshed_at = None
            self.assertTrue(worker._refresh_alpha_cache_if_needed())

            self.assertEqual(market_client.calls, 1)
            self.assertIsNotNone(worker._binance_futures_retry_after)
            worker.close()

    def test_holder_metrics_refresh_runs_as_side_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                    holder_metrics_batch_size=5,
                )
            )
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            worker._repo.upsert_token(
                "0xtoken",
                "MEME",
                "Meme",
                now,
                {"is_binance_alpha": True, "pair_seeded_at": now.isoformat()},
            )
            worker._repo.upsert_seed_pair(
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
            worker._honeypot = FakeHoneypotClient()
            worker._alpha_rank_client = FakeAlphaRankClient(
                {
                    "0xtoken": {
                        "holder_count": 4321,
                        "top10_holder_share": 0.8765,
                    }
                }
            )
            self.assertEqual(worker.refresh_holder_metrics(now=now), 1)
            metadata = worker._repo.get_token_metadata("0xtoken")
            self.assertEqual(metadata["holder_count"], 4321)
            self.assertEqual(metadata["holder_count_source"], "binance_alpha_rank")
            self.assertEqual(metadata["top10_holder_share"], 0.8765)
            self.assertEqual(metadata["top10_holder_source"], "binance_alpha_rank")
            self.assertEqual(metadata["holder_metrics_updated_at"], now.isoformat())
            self.assertEqual(metadata["holder_metrics_attempted_at"], now.isoformat())
            self.assertEqual(worker._honeypot.holder_calls, [])
            self.assertEqual(worker._honeypot.top10_calls, [])
            worker.close()

    def test_holder_metrics_refresh_falls_back_to_honeypot_when_rank_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                    holder_metrics_batch_size=5,
                )
            )
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            worker._repo.upsert_token("0xtoken", "MEME", "Meme", now, {"is_binance_alpha": True})
            worker._repo.upsert_seed_pair(
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
            worker._alpha_rank_client = FakeAlphaRankClient({})
            worker._honeypot = FakeHoneypotClient()
            self.assertEqual(worker.refresh_holder_metrics(now=now), 1)
            metadata = worker._repo.get_token_metadata("0xtoken")
            self.assertEqual(metadata["holder_count"], 1234)
            self.assertEqual(metadata["holder_count_source"], "honeypot")
            self.assertEqual(metadata["top10_holder_share"], 0.42)
            self.assertEqual(metadata["top10_holder_source"], "honeypot")
            self.assertEqual(worker._honeypot.holder_calls, ["0xtoken"])
            self.assertEqual(worker._honeypot.top10_calls, ["0xtoken"])
            worker.close()

    def test_pair_refresh_does_not_call_holder_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                )
            )
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            snapshot = PairSnapshot(
                pair_address="0xpair",
                token_address="0xtoken",
                token_symbol="MEME",
                token_name="Meme",
                quote_token_address="0xquote",
                quote_symbol="WBNB",
                observed_at=now,
                pair_created_at=now - timedelta(hours=1),
                dex_id="pancakeswap",
                pair_url="https://example.com",
                price_usd=0.01,
                price_native=0.00002,
                liquidity_usd=100_000,
                fdv=1_000_000,
                market_cap=900_000,
                volume_m5=2_000,
                volume_h1=30_000,
                volume_h24=300_000,
                buys_m5=20,
                sells_m5=5,
                buys_h1=100,
                sells_h1=40,
                price_change_m5=3,
                price_change_h1=20,
                price_change_h24=50,
                website_count=1,
                social_count=1,
                boosts_active=0,
                raw_payload={},
            )
            worker._dexscreener = FakeDexScreenerClient(snapshot=snapshot)
            worker._honeypot = FailingHoneypotClient()
            worker._alpha_token_map = {"0xtoken": alpha_token("0xtoken", holders=None)}
            worker._repo.upsert_token(
                "0xtoken",
                "MEME",
                "Meme",
                now,
                {"holder_count": 1234, "holder_count_source": "honeypot"},
            )
            worker._refresh_pair(
                {
                    "pair_address": "0xpair",
                    "token_address": "0xtoken",
                    "quote_token_address": "0xquote",
                    "quote_symbol": "WBNB",
                    "pair_created_at": (now - timedelta(hours=1)).isoformat(),
                }
            )
            metadata = worker._repo.get_token_metadata("0xtoken")
            self.assertEqual(metadata["quote_symbol"], "WBNB")
            self.assertEqual(metadata["holder_count"], 1234)
            self.assertEqual(metadata["holder_count_source"], "honeypot")
            self.assertNotIn("holder_metrics_updated_at", metadata)
            worker.close()

    def test_run_cycle_refreshes_snapshots_before_discovery_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                    max_pairs_per_cycle=1,
                    binance_alpha_seed_batch_size=1,
                )
            )
            now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
            token = alpha_token("0xtoken")
            snapshot = PairSnapshot(
                pair_address="0xpair",
                token_address="0xtoken",
                token_symbol="MEME",
                token_name="Meme",
                quote_token_address="0xquote",
                quote_symbol="WBNB",
                observed_at=now,
                pair_created_at=now - timedelta(hours=1),
                dex_id="pancakeswap",
                pair_url="https://example.com",
                price_usd=0.01,
                price_native=0.00002,
                liquidity_usd=100_000,
                fdv=1_000_000,
                market_cap=900_000,
                volume_m5=2_000,
                volume_h1=30_000,
                volume_h24=300_000,
                buys_m5=20,
                sells_m5=5,
                buys_h1=100,
                sells_h1=40,
                price_change_m5=3,
                price_change_h1=20,
                price_change_h24=50,
                website_count=1,
                social_count=1,
                boosts_active=0,
                raw_payload={},
            )
            worker._repo.upsert_token("0xtoken", "MEME", "Meme", now, {"is_binance_alpha": True})
            worker._repo.upsert_seed_pair(
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
            events: list[str] = []
            worker._alpha_client = FakeAlphaClient([token])
            worker._binance_market_client = FakeBinanceMarketClient()
            worker._dexscreener = RecordingDexScreenerClient(events, snapshot=snapshot)
            worker._discovery = RecordingFailingDiscoveryClient(events)
            worker._holder_metrics_ran_at = datetime.now(timezone.utc)

            worker.run_cycle()
            worker.run_cycle()

            self.assertLess(events.index("snapshot"), events.index("discovery"))
            self.assertEqual(worker._discovery.calls, 1)
            self.assertIsNotNone(worker._discovery_retry_after)
            worker.close()

    def test_partial_discovery_success_keeps_retry_backoff_after_later_chunk_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://127.0.0.1:8545",
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                    discovery_start_block=1,
                    discovery_block_chunk_size=1,
                )
            )
            token = alpha_token("0xtoken")
            worker._alpha_client = FakeAlphaClient([token])
            worker._binance_market_client = FakeBinanceMarketClient()
            worker._discovery = PartiallyFailingDiscoveryClient()

            self.assertEqual(worker._discover_new_pairs(), 1)

            self.assertIsNotNone(worker._discovery_retry_after)
            worker.close()

    def test_discovery_429_switches_to_next_rpc_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            response = requests.Response()
            response.status_code = 429
            worker = MonitorWorker(
                AppConfig(
                    bsc_rpc_url="http://rpc-a",
                    bsc_rpc_urls=("http://rpc-a", "http://rpc-b"),
                    factory_address="0x0000000000000000000000000000000000000001",
                    database_path=str(Path(tmpdir) / "monitor.db"),
                    monitor_universe="all",
                )
            )
            worker._discovery = FailingLatestBlockDiscoveryClient(
                requests.HTTPError("rate limited", response=response)
            )
            created_clients: list[str] = []

            def build_fake_client(rpc_url: str):
                created_clients.append(rpc_url)
                return EmptyDiscoveryClient()

            worker._build_discovery_client = build_fake_client  # type: ignore[method-assign]

            self.assertEqual(worker._discover_new_pairs(), 0)

            self.assertEqual(worker._rpc_index, 1)
            self.assertEqual(created_clients, ["http://rpc-b"])
            self.assertIsNotNone(worker._rpc_backoff_until.get("http://rpc-a"))
            worker.close()


if __name__ == "__main__":
    unittest.main()
