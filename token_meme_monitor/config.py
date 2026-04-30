from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from token_meme_monitor.constants import DEFAULT_QUOTE_TOKENS


def _load_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _env_int(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_csv(name: str) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return ()
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class SignalConfig:
    strategy_version: str = "v1"
    focus_score_threshold: int = 65
    alert_score_threshold: int = 78
    alert_cooldown_minutes: int = 30
    min_liquidity_usd: float = 15_000.0
    archive_liquidity_usd: float = 3_000.0
    min_volume_h1_usd: float = 15_000.0
    min_buy_count_m5: int = 8
    min_buy_sell_ratio_m5: float = 1.8
    focus_volume_to_liquidity_ratio: float = 0.25
    max_pair_age_hours: int = 24
    base_poll_interval_seconds: int = 60
    focus_poll_interval_seconds: int = 15


@dataclass(frozen=True)
class AppConfig:
    bsc_rpc_url: str
    factory_address: str
    database_path: str
    bsc_rpc_urls: tuple[str, ...] = ()
    chain_id: str = "bsc"
    dexscreener_base_url: str = "https://api.dexscreener.com"
    discovery_start_block: int | None = None
    discovery_initial_backfill_blocks: int = 120
    discovery_block_chunk_size: int = 250
    discovery_block_confirmations: int = 3
    worker_loop_seconds: int = 15
    max_pairs_per_cycle: int = 80
    quote_tokens: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_QUOTE_TOKENS))
    signal: SignalConfig = field(default_factory=SignalConfig)
    monitor_universe: str = "binance_alpha"
    binance_alpha_url: str = "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"
    binance_alpha_refresh_minutes: int = 1
    binance_alpha_pair_seed_refresh_minutes: int = 15
    binance_alpha_seed_batch_size: int = 30
    holder_metrics_job_interval_seconds: int = 300
    holder_metrics_refresh_hours: int = 24
    holder_metrics_batch_size: int = 5
    binance_futures_registry_refresh_hours: int = 6
    risk_enrichment_fixture_path: str = ""
    risk_enrichment_ttl_hours: int = 6
    risk_enrichment_batch_size: int = 50
    dashboard_auto_refresh_seconds: int = 20
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    log_level: str = "INFO"


def load_config(env_file: str | None = ".env") -> AppConfig:
    _load_env_file(env_file)
    signal = SignalConfig(
        strategy_version=_env_str("STRATEGY_VERSION", "v1"),
        focus_score_threshold=_env_int("FOCUS_SCORE_THRESHOLD", 65),
        alert_score_threshold=_env_int("ALERT_SCORE_THRESHOLD", 78),
        alert_cooldown_minutes=_env_int("ALERT_COOLDOWN_MINUTES", 30),
        min_liquidity_usd=_env_float("MIN_LIQUIDITY_USD", 15_000.0),
        archive_liquidity_usd=_env_float("ARCHIVE_LIQUIDITY_USD", 3_000.0),
        min_volume_h1_usd=_env_float("MIN_VOLUME_H1_USD", 15_000.0),
        min_buy_count_m5=_env_int("MIN_BUY_COUNT_M5", 8),
        min_buy_sell_ratio_m5=_env_float("MIN_BUY_SELL_RATIO_M5", 1.8),
        focus_volume_to_liquidity_ratio=_env_float("FOCUS_VOLUME_TO_LIQUIDITY_RATIO", 0.25),
        max_pair_age_hours=_env_int("MAX_PAIR_AGE_HOURS", 24),
        base_poll_interval_seconds=_env_int("BASE_POLL_INTERVAL_SECONDS", 60),
        focus_poll_interval_seconds=_env_int("FOCUS_POLL_INTERVAL_SECONDS", 15),
    )
    bsc_rpc_urls = _env_csv("BSC_RPC_URLS")
    bsc_rpc_url = _env_str("BSC_RPC_URL", "https://bnb.rpc.subquery.network/public")
    if bsc_rpc_urls:
        bsc_rpc_url = bsc_rpc_urls[0]
    else:
        bsc_rpc_urls = (bsc_rpc_url,)
    return AppConfig(
        bsc_rpc_url=bsc_rpc_url,
        bsc_rpc_urls=bsc_rpc_urls,
        factory_address=_env_str(
            "PANCAKESWAP_V2_FACTORY_ADDRESS",
            "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
        ),
        database_path=_env_str("MONITOR_DATABASE_PATH", "data/monitor.db"),
        discovery_start_block=_env_int("DISCOVERY_START_BLOCK", None),
        discovery_initial_backfill_blocks=_env_int("DISCOVERY_INITIAL_BACKFILL_BLOCKS", 120),
        discovery_block_chunk_size=_env_int("DISCOVERY_BLOCK_CHUNK_SIZE", 250),
        discovery_block_confirmations=_env_int("DISCOVERY_BLOCK_CONFIRMATIONS", 3),
        worker_loop_seconds=_env_int("WORKER_LOOP_SECONDS", 15),
        max_pairs_per_cycle=_env_int("MAX_PAIRS_PER_CYCLE", 80),
        signal=signal,
        monitor_universe=_env_str("MONITOR_UNIVERSE", "binance_alpha"),
        binance_alpha_url=_env_str(
            "BINANCE_ALPHA_URL",
            "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list",
        ),
        binance_alpha_refresh_minutes=_env_int("BINANCE_ALPHA_REFRESH_MINUTES", 1),
        binance_alpha_pair_seed_refresh_minutes=_env_int("BINANCE_ALPHA_PAIR_SEED_REFRESH_MINUTES", 15),
        binance_alpha_seed_batch_size=_env_int("BINANCE_ALPHA_SEED_BATCH_SIZE", 30),
        holder_metrics_job_interval_seconds=_env_int("HOLDER_METRICS_JOB_INTERVAL_SECONDS", 300),
        holder_metrics_refresh_hours=_env_int("HOLDER_METRICS_REFRESH_HOURS", 24),
        holder_metrics_batch_size=_env_int("HOLDER_METRICS_BATCH_SIZE", 5),
        binance_futures_registry_refresh_hours=_env_int("BINANCE_FUTURES_REGISTRY_REFRESH_HOURS", 6),
        risk_enrichment_fixture_path=_env_str("RISK_ENRICHMENT_FIXTURE_PATH", ""),
        risk_enrichment_ttl_hours=_env_int("RISK_ENRICHMENT_TTL_HOURS", 6),
        risk_enrichment_batch_size=_env_int("RISK_ENRICHMENT_BATCH_SIZE", 50),
        dashboard_auto_refresh_seconds=_env_int("DASHBOARD_AUTO_REFRESH_SECONDS", 20),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        log_level=_env_str("LOG_LEVEL", "INFO"),
    )
