from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard import app as dashboard_app
from token_meme_monitor.database import MonitorRepository


def _clear_external_trend_cache() -> None:
    clear = getattr(dashboard_app.get_external_trend_metrics, "clear", None)
    if clear is not None:
        clear()


def _before_timestamp(observed_at: datetime) -> int:
    return int(observed_at.timestamp()) + 3600


def _ohlcv_rows(observed_at: datetime) -> list[dict[str, float]]:
    observed_ts = int(observed_at.timestamp())
    return [
        {"ts": observed_ts - 24 * 3600, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100.0},
        {"ts": observed_ts - 2 * 3600, "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0, "volume": 100.0},
        {"ts": observed_ts, "open": 4.0, "high": 4.0, "low": 4.0, "close": 4.0, "volume": 100.0},
    ]


class DashboardExternalTrendTests(unittest.TestCase):
    def setUp(self) -> None:
        _clear_external_trend_cache()

    def tearDown(self) -> None:
        _clear_external_trend_cache()

    def test_zero_row_fetch_marker_does_not_block_refetch(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        pair_address = "0xpool"

        class FakeGeckoTerminalClient:
            calls = 0

            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def fetch_pool_ohlcv(self, *args: object, **kwargs: object) -> list[dict[str, float]]:
                type(self).calls += 1
                return _ohlcv_rows(observed_at)

        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = str(Path(tmpdir) / "monitor.db")
            repo = MonitorRepository(database_path)
            repo.initialize()
            repo.record_external_ohlcv_fetch(
                network=dashboard_app.EXTERNAL_TREND_NETWORK,
                pool_address=pair_address,
                timeframe="hour",
                aggregate=1,
                limit=dashboard_app.EXTERNAL_TREND_OHLCV_LIMIT,
                before_timestamp=_before_timestamp(observed_at),
                row_count=0,
            )
            repo.close()

            with patch.object(dashboard_app, "GeckoTerminalClient", FakeGeckoTerminalClient):
                result = dashboard_app.get_external_trend_metrics(database_path, pair_address, observed_at)

        self.assertEqual(FakeGeckoTerminalClient.calls, 1)
        self.assertAlmostEqual(result["external_return_2h"], 1.0)
        self.assertAlmostEqual(result["external_return_24h"], 3.0)

    def test_stale_metrics_are_recomputed_from_backfilled_ohlcv(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        observed_hour = observed_at.isoformat()
        pair_address = "0xpool"

        class FailingGeckoTerminalClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def fetch_pool_ohlcv(self, *args: object, **kwargs: object) -> list[dict[str, float]]:
                raise AssertionError("cached OHLCV rows should satisfy this request")

        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = str(Path(tmpdir) / "monitor.db")
            repo = MonitorRepository(database_path)
            repo.initialize()
            repo.upsert_external_trend_metrics(
                pair_address,
                observed_hour,
                external_return_2h=None,
                external_return_24h=None,
                raw_payload={"source": "geckoterminal_stale", "row_count": 0},
            )
            repo.upsert_external_ohlcv(
                network=dashboard_app.EXTERNAL_TREND_NETWORK,
                pool_address=pair_address,
                timeframe="hour",
                aggregate=1,
                rows=_ohlcv_rows(observed_at),
            )
            repo.record_external_ohlcv_fetch(
                network=dashboard_app.EXTERNAL_TREND_NETWORK,
                pool_address=pair_address,
                timeframe="hour",
                aggregate=1,
                limit=dashboard_app.EXTERNAL_TREND_OHLCV_LIMIT,
                before_timestamp=_before_timestamp(observed_at),
                row_count=1,
            )
            repo.close()

            with patch.object(dashboard_app, "GeckoTerminalClient", FailingGeckoTerminalClient):
                result = dashboard_app.get_external_trend_metrics(database_path, pair_address, observed_at)

            repo = MonitorRepository(database_path)
            repo.initialize()
            cached = repo.get_external_trend_metrics(pair_address, observed_hour)
            repo.close()

        self.assertAlmostEqual(result["external_return_2h"], 1.0)
        self.assertAlmostEqual(result["external_return_24h"], 3.0)
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertAlmostEqual(cached["external_return_2h"], 1.0)
        self.assertAlmostEqual(cached["external_return_24h"], 3.0)

    def test_incomplete_geckoterminal_metrics_are_recomputed_from_backfilled_ohlcv(self) -> None:
        observed_at = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        observed_hour = observed_at.isoformat()
        pair_address = "0xpool"

        class FailingGeckoTerminalClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def fetch_pool_ohlcv(self, *args: object, **kwargs: object) -> list[dict[str, float]]:
                raise AssertionError("complete cached OHLCV rows should be used before refetching")

        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = str(Path(tmpdir) / "monitor.db")
            repo = MonitorRepository(database_path)
            repo.initialize()
            repo.upsert_external_trend_metrics(
                pair_address,
                observed_hour,
                external_return_2h=1.0,
                external_return_24h=None,
                raw_payload={"source": "geckoterminal", "row_count": 3},
            )
            repo.upsert_external_ohlcv(
                network=dashboard_app.EXTERNAL_TREND_NETWORK,
                pool_address=pair_address,
                timeframe="hour",
                aggregate=1,
                rows=_ohlcv_rows(observed_at),
            )
            repo.close()

            with patch.object(dashboard_app, "GeckoTerminalClient", FailingGeckoTerminalClient):
                result = dashboard_app.get_external_trend_metrics(database_path, pair_address, observed_at)

            repo = MonitorRepository(database_path)
            repo.initialize()
            cached = repo.get_external_trend_metrics(pair_address, observed_hour)
            repo.close()

        self.assertAlmostEqual(result["external_return_2h"], 1.0)
        self.assertAlmostEqual(result["external_return_24h"], 3.0)
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertAlmostEqual(cached["external_return_24h"], 3.0)


if __name__ == "__main__":
    unittest.main()
