from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any

import requests

from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.utils import ensure_parent_dir, safe_float


GECKO_BASE = "https://api.geckoterminal.com/api/v2"
REQUEST_HEADERS = {"accept": "application/json", "User-Agent": "token-meme-monitor/1.0"}


@dataclass
class TokenValidationResult:
    token_address: str
    token_symbol: str
    token_name: str
    pool_address: str | None
    pool_name: str | None
    surge_anchor_at: str | None
    surge_max_return_24h: float | None
    price_before: float | None
    est_market_cap_before: float | None
    est_fdv_before: float | None
    current_liquidity_usd: float | None
    current_holders: int | None
    h1_return_before: float | None
    h4_return_before: float | None
    h24_return_before: float | None
    volume_h1_before: float | None
    volume_h24_before: float | None
    volume_impulse_vs_prev24h: float | None
    volume_impulse_vs_prev72h: float | None
    market_cap_bucket: str
    current_like_hits: list[str]
    relative_hits: list[str]
    verdict: str
    notes: list[str]


class GeckoTerminalBacktester:
    def __init__(
        self,
        network: str = "bsc",
        sleep_seconds: float = 4.2,
        max_retries: int = 6,
        database_path: str | None = None,
    ) -> None:
        self._network = network
        self._sleep_seconds = sleep_seconds
        self._max_retries = max_retries
        self._database_path = database_path
        self._session = requests.Session()

    def run_for_tokens(self, token_addresses: list[str]) -> list[TokenValidationResult]:
        results: list[TokenValidationResult] = []
        for token_address in token_addresses:
            token_address = token_address.strip().lower()
            if not token_address:
                continue
            results.append(self._validate_token(token_address))
        return results

    def write_outputs(self, results: list[TokenValidationResult], *, json_path: str, markdown_path: str) -> None:
        ensure_parent_dir(json_path)
        ensure_parent_dir(markdown_path)
        Path(json_path).write_text(
            json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        Path(markdown_path).write_text(self._render_markdown(results), encoding="utf-8")

    def _validate_token(self, token_address: str) -> TokenValidationResult:
        token_payload = self._get_json(f"/networks/{self._network}/tokens/{token_address}")
        token_data = (token_payload or {}).get("data") or {}
        token_attr = token_data.get("attributes") or {}
        token_name = str(token_attr.get("name") or token_address[:8])
        token_symbol = str(token_attr.get("symbol") or token_address[:8])

        top_pools = (((token_data.get("relationships") or {}).get("top_pools") or {}).get("data")) or []
        if not top_pools:
            return TokenValidationResult(
                token_address=token_address,
                token_symbol=token_symbol,
                token_name=token_name,
                pool_address=None,
                pool_name=None,
                surge_anchor_at=None,
                surge_max_return_24h=None,
                price_before=None,
                est_market_cap_before=None,
                est_fdv_before=None,
                current_liquidity_usd=None,
                current_holders=_safe_int(token_attr.get("holders")),
                h1_return_before=None,
                h4_return_before=None,
                h24_return_before=None,
                volume_h1_before=None,
                volume_h24_before=None,
                volume_impulse_vs_prev24h=None,
                volume_impulse_vs_prev72h=None,
                market_cap_bucket="unknown",
                current_like_hits=[],
                relative_hits=[],
                verdict="数据不足",
                notes=["未找到可交易池"],
            )
        pool_id = str((top_pools[0] or {}).get("id") or "")
        pool_address = _pool_id_to_address(pool_id)
        pool_payload = self._get_json(f"/networks/{self._network}/pools/{pool_address}")
        pool_data = (pool_payload or {}).get("data") or {}
        pool_attr = pool_data.get("attributes") or {}
        pool_name = str(pool_attr.get("name") or pool_address)
        current_liquidity_usd = safe_float(pool_attr.get("reserve_in_usd"))
        current_holders = _safe_int(token_attr.get("holders"))

        daily = self._fetch_ohlcv(pool_address, timeframe="day", aggregate=1, limit=90)
        if len(daily) < 14:
            return TokenValidationResult(
                token_address=token_address,
                token_symbol=token_symbol,
                token_name=token_name,
                pool_address=pool_address,
                pool_name=pool_name,
                surge_anchor_at=None,
                surge_max_return_24h=None,
                price_before=None,
                est_market_cap_before=None,
                est_fdv_before=None,
                current_liquidity_usd=current_liquidity_usd,
                current_holders=current_holders,
                h1_return_before=None,
                h4_return_before=None,
                h24_return_before=None,
                volume_h1_before=None,
                volume_h24_before=None,
                volume_impulse_vs_prev24h=None,
                volume_impulse_vs_prev72h=None,
                market_cap_bucket="unknown",
                current_like_hits=[],
                relative_hits=[],
                verdict="数据不足",
                notes=["90 天日线样本不足 14 根"],
            )

        daily_anchor_index, _ = _find_surge_anchor(daily, forward_bars=7)
        daily_anchor = daily[daily_anchor_index]
        hourly = self._fetch_ohlcv(
            pool_address,
            timeframe="hour",
            aggregate=1,
            limit=200,
            before_timestamp=daily_anchor["ts"] + 48 * 3600,
        )
        hourly = [item for item in hourly if daily_anchor["ts"] - 72 * 3600 <= item["ts"] <= daily_anchor["ts"] + 48 * 3600]
        if len(hourly) < 30:
            return TokenValidationResult(
                token_address=token_address,
                token_symbol=token_symbol,
                token_name=token_name,
                pool_address=pool_address,
                pool_name=pool_name,
                surge_anchor_at=_ts_to_iso(daily_anchor["ts"]),
                surge_max_return_24h=None,
                price_before=None,
                est_market_cap_before=None,
                est_fdv_before=None,
                current_liquidity_usd=current_liquidity_usd,
                current_holders=current_holders,
                h1_return_before=None,
                h4_return_before=None,
                h24_return_before=None,
                volume_h1_before=None,
                volume_h24_before=None,
                volume_impulse_vs_prev24h=None,
                volume_impulse_vs_prev72h=None,
                market_cap_bucket="unknown",
                current_like_hits=[],
                relative_hits=[],
                verdict="数据不足",
                notes=["主升浪附近小时线样本不足 30 根"],
            )

        anchor_index = _find_hour_anchor(hourly, daily_anchor["ts"])
        surge_max_return_24h = _forward_max_return(hourly, anchor_index, forward_bars=24)
        anchor = hourly[anchor_index]
        price_before = anchor["close"]

        normalized_total_supply = safe_float(token_attr.get("normalized_total_supply"))
        current_price = safe_float(token_attr.get("price_usd"))
        current_market_cap = safe_float(token_attr.get("market_cap_usd"))

        est_total_supply = normalized_total_supply
        est_circulating_supply = None
        if current_market_cap and current_price and current_price > 0:
            est_circulating_supply = current_market_cap / current_price

        est_market_cap_before = price_before * est_circulating_supply if est_circulating_supply else None
        est_fdv_before = price_before * est_total_supply if est_total_supply else None

        h1_return_before = _return(hourly, anchor_index, 1)
        h4_return_before = _return(hourly, anchor_index, 4)
        h24_return_before = _return(hourly, anchor_index, 24)
        volume_h1_before = anchor["volume"]
        volume_h24_before = sum(item["volume"] for item in hourly[max(0, anchor_index - 23) : anchor_index + 1])
        volume_impulse_vs_prev24h = _volume_impulse(hourly, anchor_index, 24)
        volume_impulse_vs_prev72h = _volume_impulse(hourly, anchor_index, 72)

        market_cap_bucket = _market_cap_bucket(est_market_cap_before or est_fdv_before)

        current_like_hits: list[str] = []
        if volume_h1_before is not None and volume_h1_before >= 15_000:
            current_like_hits.append("h1_volume_support")
        if h1_return_before is not None and h1_return_before >= 0.20:
            current_like_hits.append("positive_price_trend_strict")
        if current_liquidity_usd is not None and 15_000 <= current_liquidity_usd <= 250_000:
            current_like_hits.append("healthy_liquidity_band_approx")
        elif current_liquidity_usd is not None and current_liquidity_usd > 250_000:
            current_like_hits.append("ample_liquidity_approx")
        liquidity_to_fdv = (
            current_liquidity_usd / est_fdv_before
            if current_liquidity_usd is not None and est_fdv_before not in (None, 0)
            else None
        )
        if liquidity_to_fdv is not None and 0.04 <= liquidity_to_fdv <= 0.40:
            current_like_hits.append("balanced_liquidity_to_fdv_approx")

        relative_hits: list[str] = []
        if volume_impulse_vs_prev24h is not None and volume_impulse_vs_prev24h >= 3:
            relative_hits.append("volume_impulse_vs_prev24h")
        if volume_impulse_vs_prev72h is not None and volume_impulse_vs_prev72h >= 2:
            relative_hits.append("volume_impulse_vs_prev72h")
        if h4_return_before is not None and h4_return_before > 0:
            relative_hits.append("positive_h4_trend")
        if h24_return_before is not None and h24_return_before > 0:
            relative_hits.append("positive_h24_trend")

        if len(current_like_hits) >= 2 and len(relative_hits) >= 2:
            verdict = "大致对上"
        elif len(current_like_hits) >= 1 or len(relative_hits) >= 2:
            verdict = "部分对上"
        else:
            verdict = "对不上或证据不足"

        notes = [
            "成交量和价格动能来自历史 OHLCV，可回测",
            "流动性、FDV、持币人数采用当前或近似结构值，不是严格历史值",
        ]
        return TokenValidationResult(
            token_address=token_address,
            token_symbol=token_symbol,
            token_name=token_name,
            pool_address=pool_address,
            pool_name=pool_name,
            surge_anchor_at=_ts_to_iso(anchor["ts"]),
            surge_max_return_24h=surge_max_return_24h,
            price_before=price_before,
            est_market_cap_before=est_market_cap_before,
            est_fdv_before=est_fdv_before,
            current_liquidity_usd=current_liquidity_usd,
            current_holders=current_holders,
            h1_return_before=h1_return_before,
            h4_return_before=h4_return_before,
            h24_return_before=h24_return_before,
            volume_h1_before=volume_h1_before,
            volume_h24_before=volume_h24_before,
            volume_impulse_vs_prev24h=volume_impulse_vs_prev24h,
            volume_impulse_vs_prev72h=volume_impulse_vs_prev72h,
            market_cap_bucket=market_cap_bucket,
            current_like_hits=current_like_hits,
            relative_hits=relative_hits,
            verdict=verdict,
            notes=notes,
        )

    def _fetch_ohlcv(
        self,
        pool_address: str,
        *,
        timeframe: str,
        aggregate: int,
        limit: int,
        before_timestamp: int | None = None,
    ) -> list[dict[str, float]]:
        cached_rows = self._read_cached_ohlcv(
            pool_address,
            timeframe=timeframe,
            aggregate=aggregate,
            limit=limit,
            before_timestamp=before_timestamp,
        )
        if cached_rows is not None:
            return cached_rows

        path = f"/networks/{self._network}/pools/{pool_address}/ohlcv/{timeframe}?aggregate={aggregate}&limit={limit}"
        if before_timestamp is not None:
            path += f"&before_timestamp={before_timestamp}"
        payload = self._get_json(path)
        page = (((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        ordered = []
        for ts, open_, high, low, close, volume in page:
            ordered.append(
                {
                    "ts": int(ts),
                    "open": float(open_),
                    "high": float(high),
                    "low": float(low),
                    "close": float(close),
                    "volume": float(volume),
                }
            )
        ordered = sorted(ordered, key=lambda item: item["ts"])
        self._write_cached_ohlcv(
            pool_address,
            timeframe=timeframe,
            aggregate=aggregate,
            limit=limit,
            before_timestamp=before_timestamp,
            rows=ordered,
        )
        return ordered

    def _read_cached_ohlcv(
        self,
        pool_address: str,
        *,
        timeframe: str,
        aggregate: int,
        limit: int,
        before_timestamp: int | None,
    ) -> list[dict[str, float]] | None:
        if not self._database_path:
            return None
        read_before = before_timestamp if before_timestamp is not None else _closed_before_timestamp(timeframe, aggregate)
        repo = MonitorRepository(self._database_path)
        repo.initialize()
        try:
            cached_rows = repo.list_external_ohlcv(
                network=self._network,
                pool_address=pool_address,
                timeframe=timeframe,
                aggregate=aggregate,
                before_timestamp=read_before,
                limit=limit,
            )
            if before_timestamp is not None:
                recorded_count = repo.get_external_ohlcv_fetch_row_count(
                    network=self._network,
                    pool_address=pool_address,
                    timeframe=timeframe,
                    aggregate=aggregate,
                    limit=limit,
                    before_timestamp=before_timestamp,
                )
                if recorded_count is not None and len(cached_rows) >= recorded_count:
                    return cached_rows[-recorded_count:] if recorded_count else []
                if len(cached_rows) >= limit:
                    return cached_rows
                return None
            recorded_count = repo.get_external_ohlcv_fetch_row_count(
                network=self._network,
                pool_address=pool_address,
                timeframe=timeframe,
                aggregate=aggregate,
                limit=limit,
                before_timestamp=read_before,
            )
            if recorded_count is not None and len(cached_rows) >= recorded_count:
                return cached_rows[-recorded_count:] if recorded_count else []
            if _cached_latest_window_is_complete(cached_rows, timeframe, aggregate, limit):
                return cached_rows
            return None
        finally:
            repo.close()

    def _write_cached_ohlcv(
        self,
        pool_address: str,
        *,
        timeframe: str,
        aggregate: int,
        limit: int,
        before_timestamp: int | None,
        rows: list[dict[str, float]],
    ) -> None:
        if not self._database_path:
            return
        closed_before = _closed_before_timestamp(timeframe, aggregate)
        closed_rows = [row for row in rows if int(row.get("ts") or 0) < closed_before]
        repo = MonitorRepository(self._database_path)
        repo.initialize()
        try:
            repo.upsert_external_ohlcv(
                network=self._network,
                pool_address=pool_address,
                timeframe=timeframe,
                aggregate=aggregate,
                rows=closed_rows,
            )
            if before_timestamp is not None and len(closed_rows) == len(rows):
                repo.record_external_ohlcv_fetch(
                    network=self._network,
                    pool_address=pool_address,
                    timeframe=timeframe,
                    aggregate=aggregate,
                    limit=limit,
                    before_timestamp=before_timestamp,
                    row_count=len(rows),
                )
            elif before_timestamp is None and len(closed_rows) == len(rows):
                repo.record_external_ohlcv_fetch(
                    network=self._network,
                    pool_address=pool_address,
                    timeframe=timeframe,
                    aggregate=aggregate,
                    limit=limit,
                    before_timestamp=closed_before,
                    row_count=len(rows),
                )
        finally:
            repo.close()

    def _get_json(self, path: str) -> dict[str, Any]:
        url = f"{GECKO_BASE}{path}"
        delay = self._sleep_seconds
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            if attempt > 0:
                time.sleep(delay)
                delay *= 1.8
            response = self._session.get(url, timeout=20, headers=REQUEST_HEADERS)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                last_error = requests.HTTPError(f"429 rate limited for {url}", response=response)
                continue
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                last_error = exc
                break
            return response.json()
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"failed to fetch {url}")

    def _render_markdown(self, results: list[TokenValidationResult]) -> str:
        lines: list[str] = [
            "# Token List 历史验证报告",
            "",
            "说明：",
            "- 监控逻辑基线来自 `docs/signal-indicator-baseline.md`。",
            "- 本次回测优先使用官方 GeckoTerminal 历史 OHLCV。",
            "- 由于官方公开历史源不提供完整的历史买卖笔数、历史持币人数、历史流动性快照，因此本报告分为“当前规则近似命中”和“相对强度验证”两部分。",
            "",
        ]

        if not results:
            lines.append("没有可分析的 token。")
            return "\n".join(lines)

        verdict_counts: dict[str, int] = {}
        bucket_counts: dict[str, int] = {}
        for result in results:
            verdict_counts[result.verdict] = verdict_counts.get(result.verdict, 0) + 1
            bucket_counts[result.market_cap_bucket] = bucket_counts.get(result.market_cap_bucket, 0) + 1

        lines.extend(
            [
                "## 总结",
                "",
                f"- 样本数：`{len(results)}`",
                f"- 结果分布：`{verdict_counts}`",
                f"- 市值桶分布：`{bucket_counts}`",
                "",
                "## 明细",
                "",
            ]
        )

        for result in results:
            lines.extend(
                [
                    f"### {result.token_symbol} ({result.token_address})",
                    "",
                    f"- 结论：`{result.verdict}`",
                    f"- 主池：`{result.pool_name or 'N/A'}` / `{result.pool_address or 'N/A'}`",
                    f"- 疯涨前锚点：`{result.surge_anchor_at or 'N/A'}`",
                    f"- 未来 24h 最大涨幅：`{_fmt_pct(result.surge_max_return_24h)}`",
                    f"- 估算上涨前市值：`{_fmt_money(result.est_market_cap_before)}`",
                    f"- 估算上涨前 FDV：`{_fmt_money(result.est_fdv_before)}`",
                    f"- 当前流动性：`{_fmt_money(result.current_liquidity_usd)}`",
                    f"- 当前持币人数：`{result.current_holders if result.current_holders is not None else 'N/A'}`",
                    f"- 上涨前价格：`{_fmt_price(result.price_before)}`",
                    f"- 上涨前 1h/4h/24h 涨幅：`{_fmt_pct(result.h1_return_before)}` / `{_fmt_pct(result.h4_return_before)}` / `{_fmt_pct(result.h24_return_before)}`",
                    f"- 上涨前 1h 成交额：`{_fmt_money(result.volume_h1_before)}`",
                    f"- 上涨前 24h 成交额：`{_fmt_money(result.volume_h24_before)}`",
                    f"- 1h 成交量相对过去 24h 中位数：`{_fmt_ratio(result.volume_impulse_vs_prev24h)}`",
                    f"- 1h 成交量相对过去 72h 中位数：`{_fmt_ratio(result.volume_impulse_vs_prev72h)}`",
                    f"- 市值桶：`{result.market_cap_bucket}`",
                    f"- 当前规则近似命中：`{', '.join(result.current_like_hits) if result.current_like_hits else '无'}`",
                    f"- 相对强度命中：`{', '.join(result.relative_hits) if result.relative_hits else '无'}`",
                    f"- 备注：`{'；'.join(result.notes)}`",
                    "",
                ]
            )
        return "\n".join(lines)


def _find_surge_anchor(hourly: list[dict[str, float]], forward_bars: int) -> tuple[int, float | None]:
    best_index = 0
    best_return = -1.0
    start_index = min(max(24, 1), max(len(hourly) - forward_bars - 1, 0))
    end_index = max(len(hourly) - forward_bars, 1)
    for index in range(start_index, end_index):
        close_price = hourly[index]["close"]
        if close_price <= 0:
            continue
        forward = hourly[index + 1 : index + 1 + forward_bars]
        if not forward:
            continue
        max_high = max(item["high"] for item in forward)
        forward_return = max_high / close_price - 1
        if forward_return > best_return:
            best_return = forward_return
            best_index = index
    return best_index, (best_return if best_return >= 0 else None)


def _forward_max_return(hourly: list[dict[str, float]], index: int, forward_bars: int) -> float | None:
    if index < 0 or index >= len(hourly):
        return None
    close_price = hourly[index]["close"]
    if close_price <= 0:
        return None
    forward = hourly[index + 1 : index + 1 + forward_bars]
    if not forward:
        return None
    return max(item["high"] for item in forward) / close_price - 1


def _return(hourly: list[dict[str, float]], index: int, lookback_hours: int) -> float | None:
    if index - lookback_hours < 0:
        return None
    previous = hourly[index - lookback_hours]["close"]
    current = hourly[index]["close"]
    if previous <= 0:
        return None
    return current / previous - 1


def _volume_impulse(hourly: list[dict[str, float]], index: int, lookback_hours: int) -> float | None:
    if index - lookback_hours < 0:
        return None
    baseline = [item["volume"] for item in hourly[index - lookback_hours : index] if item["volume"] >= 0]
    if not baseline:
        return None
    baseline_median = median(baseline)
    if baseline_median <= 0:
        return None
    return hourly[index]["volume"] / baseline_median


def _market_cap_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 1_000_000:
        return "<1M"
    if value < 10_000_000:
        return "1M-10M"
    if value < 50_000_000:
        return "10M-50M"
    return "50M+"


def _pool_id_to_address(pool_id: str) -> str:
    if "_" in pool_id:
        return pool_id.split("_", 1)[1]
    return pool_id


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ts_to_iso(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:,.2f}"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "N/A"
    if value >= 1:
        return f"${value:,.4f}"
    if value >= 0.01:
        return f"${value:,.6f}"
    return f"${value:,.8f}"


def _fmt_ratio(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}x"


def _timeframe_seconds(timeframe: str, aggregate: int) -> int:
    if timeframe == "day":
        return 86_400 * aggregate
    if timeframe == "hour":
        return 3_600 * aggregate
    if timeframe == "minute":
        return 60 * aggregate
    raise ValueError(f"unsupported timeframe: {timeframe}")


def _closed_before_timestamp(timeframe: str, aggregate: int) -> int:
    seconds = _timeframe_seconds(timeframe, aggregate)
    now_ts = int(time.time())
    return (now_ts // seconds) * seconds


def _cached_latest_window_is_complete(
    cached_rows: list[dict[str, float]],
    timeframe: str,
    aggregate: int,
    limit: int,
) -> bool:
    if len(cached_rows) < limit:
        return False
    latest_closed_ts = _closed_before_timestamp(timeframe, aggregate) - _timeframe_seconds(timeframe, aggregate)
    return int(cached_rows[-1].get("ts") or 0) >= latest_closed_ts


def _find_hour_anchor(hourly: list[dict[str, float]], target_ts: int) -> int:
    candidates = [index for index, item in enumerate(hourly) if item["ts"] <= target_ts]
    if not candidates:
        return 0
    return candidates[-1]
