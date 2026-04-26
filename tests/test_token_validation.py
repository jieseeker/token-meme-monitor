from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from token_meme_monitor.token_validation import GeckoTerminalBacktester


class StubBacktester(GeckoTerminalBacktester):
    def __init__(self, *, token_payload: dict, pool_payload: dict, daily_rows: list[dict], hourly_rows: list[dict]) -> None:
        super().__init__(sleep_seconds=4.2, max_retries=1)
        self._token_payload = token_payload
        self._pool_payload = pool_payload
        self._daily_rows = daily_rows
        self._hourly_rows = hourly_rows

    def _get_json(self, path: str) -> dict:
        if "/tokens/" in path:
            return self._token_payload
        if "/pools/" in path and "/ohlcv/" not in path:
            return self._pool_payload
        raise AssertionError(f"unexpected path: {path}")

    def _fetch_ohlcv(
        self,
        pool_address: str,
        *,
        timeframe: str,
        aggregate: int,
        limit: int,
        before_timestamp: int | None = None,
    ) -> list[dict[str, float]]:
        del pool_address, aggregate, limit, before_timestamp
        if timeframe == "day":
            return self._daily_rows
        if timeframe == "hour":
            return self._hourly_rows
        raise AssertionError(f"unexpected timeframe: {timeframe}")


class CachedOhlcvBacktester(GeckoTerminalBacktester):
    def __init__(self, *, database_path: str) -> None:
        super().__init__(network="bsc", sleep_seconds=4.2, max_retries=1, database_path=database_path)
        self.ohlcv_requests = 0

    def _get_json(self, path: str) -> dict:
        if "/ohlcv/" not in path:
            return {}
        self.ohlcv_requests += 1
        base = datetime(2026, 4, 24, 10, tzinfo=timezone.utc)
        return {
            "data": {
                "attributes": {
                    "ohlcv_list": [
                        [int(base.timestamp()), 1.0, 1.2, 0.9, 1.1, 100.0],
                        [int((base + timedelta(hours=1)).timestamp()), 1.1, 1.4, 1.0, 1.3, 200.0],
                    ]
                }
            }
        }


class TokenValidationTests(unittest.TestCase):
    def test_sparse_hourly_history_returns_data_insufficient_instead_of_crashing(self) -> None:
        base = datetime(2026, 4, 1, tzinfo=timezone.utc)
        daily_rows = []
        for index in range(40):
            ts = int((base + timedelta(days=index)).timestamp())
            close = 1.0 + index * 0.01
            daily_rows.append(
                {
                    "ts": ts,
                    "open": close,
                    "high": close * 1.10,
                    "low": close * 0.95,
                    "close": close,
                    "volume": 1_000.0 + index,
                }
            )
        anchor_day = base + timedelta(days=24)
        hourly_rows = []
        for index in range(10):
            ts = int((anchor_day + timedelta(hours=index - 5)).timestamp())
            close = 1.5 + index * 0.01
            hourly_rows.append(
                {
                    "ts": ts,
                    "open": close,
                    "high": close * 1.02,
                    "low": close * 0.98,
                    "close": close,
                    "volume": 500.0 + index,
                }
            )

        backtester = StubBacktester(
            token_payload={
                "data": {
                    "attributes": {"name": "Meme", "symbol": "MEME", "holders": "321"},
                    "relationships": {"top_pools": {"data": [{"id": "bsc_0xpool"}]}},
                }
            },
            pool_payload={"data": {"attributes": {"name": "MEME/WBNB", "reserve_in_usd": "12345"}}},
            daily_rows=daily_rows,
            hourly_rows=hourly_rows,
        )

        result = backtester._validate_token("0xtoken")

        self.assertEqual(result.verdict, "数据不足")
        self.assertEqual(result.notes, ["主升浪附近小时线样本不足 30 根"])
        self.assertEqual(result.current_liquidity_usd, 12345.0)
        self.assertEqual(result.current_holders, 321)

    def test_first_request_does_not_sleep_before_calling_geckoterminal(self) -> None:
        response = Mock()
        response.status_code = 200
        response.headers = {}
        response.raise_for_status = Mock()
        response.json.return_value = {"ok": True}

        backtester = GeckoTerminalBacktester(sleep_seconds=4.2, max_retries=1)
        backtester._session = Mock()
        backtester._session.get.return_value = response

        with patch("token_meme_monitor.token_validation.time.sleep") as sleep_mock:
            payload = backtester._get_json("/networks/bsc/ping")

        self.assertEqual(payload, {"ok": True})
        sleep_mock.assert_not_called()
        backtester._session.get.assert_called_once()

    def test_historical_ohlcv_fetches_are_read_from_sqlite_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            backtester = CachedOhlcvBacktester(database_path=str(Path(tmpdir) / "monitor.db"))
            before_timestamp = int(datetime(2026, 4, 24, 13, tzinfo=timezone.utc).timestamp())

            first = backtester._fetch_ohlcv(
                "0xpool",
                timeframe="hour",
                aggregate=1,
                limit=48,
                before_timestamp=before_timestamp,
            )
            second = backtester._fetch_ohlcv(
                "0xpool",
                timeframe="hour",
                aggregate=1,
                limit=48,
                before_timestamp=before_timestamp,
            )

            self.assertEqual(backtester.ohlcv_requests, 1)
            self.assertEqual(first, second)
            self.assertEqual([row["ts"] for row in second], [int(datetime(2026, 4, 24, 10, tzinfo=timezone.utc).timestamp()), int(datetime(2026, 4, 24, 11, tzinfo=timezone.utc).timestamp())])

    def test_short_latest_ohlcv_window_is_cached_until_next_candle_closes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            backtester = CachedOhlcvBacktester(database_path=str(Path(tmpdir) / "monitor.db"))
            now_ts = int(datetime(2026, 4, 24, 12, 30, tzinfo=timezone.utc).timestamp())

            with patch("token_meme_monitor.token_validation.time.time", return_value=now_ts):
                first = backtester._fetch_ohlcv("0xpool", timeframe="hour", aggregate=1, limit=200)
                second = backtester._fetch_ohlcv("0xpool", timeframe="hour", aggregate=1, limit=200)

            self.assertEqual(backtester.ohlcv_requests, 1)
            self.assertEqual(first, second)
            self.assertEqual(len(second), 2)

    def test_surge_max_return_uses_selected_anchor_hour(self) -> None:
        base = datetime(2026, 4, 1, tzinfo=timezone.utc)
        daily_rows = []
        for index in range(40):
            high = 1.1
            if 25 <= index <= 31:
                high = 3.0
            ts = int((base + timedelta(days=index)).timestamp())
            daily_rows.append(
                {
                    "ts": ts,
                    "open": 1.0,
                    "high": high,
                    "low": 0.9,
                    "close": 1.0,
                    "volume": 1_000.0,
                }
            )
        anchor_day = base + timedelta(days=24)
        hourly_rows = []
        for index in range(61):
            ts = int((anchor_day + timedelta(hours=index - 30)).timestamp())
            close = 10.0 if index == 30 else 1.0
            high = 20.0 if 31 <= index <= 54 else 1.2
            if 25 <= index <= 29:
                high = 100.0
            hourly_rows.append(
                {
                    "ts": ts,
                    "open": close,
                    "high": high,
                    "low": close * 0.9,
                    "close": close,
                    "volume": 1_000.0,
                }
            )
        backtester = StubBacktester(
            token_payload={
                "data": {
                    "attributes": {"name": "Meme", "symbol": "MEME", "holders": "321"},
                    "relationships": {"top_pools": {"data": [{"id": "bsc_0xpool"}]}},
                }
            },
            pool_payload={"data": {"attributes": {"name": "MEME/WBNB", "reserve_in_usd": "12345"}}},
            daily_rows=daily_rows,
            hourly_rows=hourly_rows,
        )

        result = backtester._validate_token("0xtoken")

        self.assertEqual(result.surge_anchor_at, "2026-04-25 00:00:00 UTC")
        self.assertAlmostEqual(result.surge_max_return_24h or 0.0, 1.0)


if __name__ == "__main__":
    unittest.main()
