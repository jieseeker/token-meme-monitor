from __future__ import annotations

import sqlite3
import zlib
from datetime import datetime, timedelta
from typing import Any

from token_meme_monitor.models import DiscoveredPair, PairSnapshot, PredictionResult, SignalDecision
from token_meme_monitor.utils import ensure_parent_dir, isoformat_utc, json_dumps, json_loads, parse_datetime, utcnow


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS scan_cursors (
        cursor_key TEXT PRIMARY KEY,
        cursor_value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tokens (
        token_address TEXT PRIMARY KEY,
        symbol TEXT,
        name TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pairs (
        pair_address TEXT PRIMARY KEY,
        chain_id TEXT NOT NULL,
        dex_id TEXT,
        token_address TEXT NOT NULL,
        token_symbol TEXT,
        token_name TEXT,
        quote_token_address TEXT NOT NULL,
        quote_symbol TEXT NOT NULL,
        token0_address TEXT NOT NULL,
        token1_address TEXT NOT NULL,
        pair_created_at TEXT NOT NULL,
        discovered_at TEXT NOT NULL,
        first_seen_block INTEGER NOT NULL,
        first_seen_tx_hash TEXT NOT NULL,
        first_seen_log_index INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'new',
        active INTEGER NOT NULL DEFAULT 1,
        risk_flags TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        last_snapshot_at TEXT,
        next_refresh_at TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pair_address TEXT NOT NULL,
        token_address TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        price_usd REAL,
        price_native REAL,
        liquidity_usd REAL,
        fdv REAL,
        market_cap REAL,
        volume_m5 REAL NOT NULL,
        volume_h1 REAL NOT NULL,
        volume_h24 REAL NOT NULL,
        buys_m5 INTEGER NOT NULL,
        sells_m5 INTEGER NOT NULL,
        buys_h1 INTEGER NOT NULL,
        sells_h1 INTEGER NOT NULL,
        price_change_m5 REAL NOT NULL,
        price_change_h1 REAL NOT NULL,
        price_change_h24 REAL NOT NULL,
        website_count INTEGER NOT NULL,
        social_count INTEGER NOT NULL,
        boosts_active INTEGER NOT NULL,
        age_minutes REAL NOT NULL,
        risk_flags TEXT NOT NULL,
        raw_json TEXT NOT NULL,
        UNIQUE(pair_address, observed_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pair_address TEXT NOT NULL,
        token_address TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        strategy_version TEXT NOT NULL,
        score INTEGER NOT NULL,
        pair_state TEXT NOT NULL,
        should_alert INTEGER NOT NULL,
        reasons TEXT NOT NULL,
        risk_flags TEXT NOT NULL,
        feature_json TEXT NOT NULL,
        UNIQUE(pair_address, observed_at, strategy_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshot_hourly_rollups (
        pair_address TEXT NOT NULL,
        token_address TEXT NOT NULL,
        observed_at_hour TEXT NOT NULL,
        first_observed_at TEXT NOT NULL,
        last_observed_at TEXT NOT NULL,
        sample_count INTEGER NOT NULL,
        open_price_usd REAL,
        high_price_usd REAL,
        low_price_usd REAL,
        close_price_usd REAL,
        avg_liquidity_usd REAL,
        max_liquidity_usd REAL,
        max_volume_h1 REAL,
        max_volume_h24 REAL,
        sum_volume_m5 REAL NOT NULL,
        sum_buys_m5 INTEGER NOT NULL,
        sum_sells_m5 INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(pair_address, observed_at_hour)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshot_raw_archives (
        snapshot_id INTEGER PRIMARY KEY,
        archived_at TEXT NOT NULL,
        compression TEXT NOT NULL,
        raw_json_z BLOB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_feature_archives (
        signal_id INTEGER PRIMARY KEY,
        archived_at TEXT NOT NULL,
        compression TEXT NOT NULL,
        feature_json_z BLOB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        sent_at TEXT NOT NULL,
        delivery_state TEXT NOT NULL,
        provider_message_id TEXT,
        error_text TEXT,
        UNIQUE(signal_id, channel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS outcomes (
        signal_id INTEGER PRIMARY KEY,
        evaluated_at TEXT NOT NULL,
        horizon_2h_return REAL,
        horizon_24h_return REAL,
        max_return_2h REAL,
        max_return_24h REAL,
        survived_24h INTEGER,
        sample_count_2h INTEGER NOT NULL,
        sample_count_24h INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_trend_metrics (
        pair_address TEXT NOT NULL,
        observed_at_hour TEXT NOT NULL,
        source TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        external_return_2h REAL,
        external_return_24h REAL,
        raw_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(pair_address, observed_at_hour, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_ohlcv (
        network TEXT NOT NULL,
        pool_address TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        aggregate INTEGER NOT NULL,
        source TEXT NOT NULL,
        ts INTEGER NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume REAL NOT NULL,
        fetched_at TEXT NOT NULL,
        PRIMARY KEY(network, pool_address, timeframe, aggregate, source, ts)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_ohlcv_fetches (
        network TEXT NOT NULL,
        pool_address TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        aggregate INTEGER NOT NULL,
        source TEXT NOT NULL,
        limit_count INTEGER NOT NULL,
        before_timestamp INTEGER NOT NULL,
        fetched_at TEXT NOT NULL,
        row_count INTEGER NOT NULL,
        PRIMARY KEY(network, pool_address, timeframe, aggregate, source, limit_count, before_timestamp)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_json_cache (
        cache_key TEXT PRIMARY KEY,
        fetched_at TEXT NOT NULL,
        value_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_predictions (
        signal_id INTEGER PRIMARY KEY,
        pair_address TEXT NOT NULL,
        token_address TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        predictor_version TEXT NOT NULL,
        prob_2h_up20 REAL NOT NULL,
        prob_6h_up50 REAL NOT NULL,
        prob_24h_up100 REAL NOT NULL,
        risk_6h_dd30 REAL NOT NULL,
        opportunity_score INTEGER NOT NULL,
        short_momentum_score INTEGER,
        continuation_score INTEGER,
        breakout_score INTEGER,
        stage TEXT NOT NULL,
        reasons_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_prediction_outcomes (
        signal_id INTEGER PRIMARY KEY,
        evaluated_at TEXT NOT NULL,
        outcome_source TEXT NOT NULL DEFAULT 'unknown',
        base_price_source TEXT NOT NULL DEFAULT 'unknown',
        base_price_usd REAL,
        gecko_base_close_usd REAL,
        price_divergence_pct REAL,
        quality_flags_json TEXT NOT NULL DEFAULT '[]',
        max_return_2h REAL,
        max_return_6h REAL,
        max_return_24h REAL,
        min_return_6h REAL,
        hit_2h_up20 INTEGER NOT NULL,
        hit_6h_up50 INTEGER NOT NULL,
        hit_24h_up100 INTEGER NOT NULL,
        hit_6h_dd30 INTEGER NOT NULL,
        sample_count_2h INTEGER NOT NULL,
        sample_count_6h INTEGER NOT NULL,
        sample_count_24h INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pairs_due ON pairs(active, next_refresh_at)",
    "CREATE INDEX IF NOT EXISTS idx_pairs_token ON pairs(token_address)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_pair_time ON snapshots(pair_address, observed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_observed_at ON snapshots(observed_at)",
    "CREATE INDEX IF NOT EXISTS idx_signals_pair_time ON signals(pair_address, observed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_signals_observed_at ON signals(observed_at)",
    "CREATE INDEX IF NOT EXISTS idx_snapshot_hourly_rollups_pair_time ON snapshot_hourly_rollups(pair_address, observed_at_hour DESC)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_sent_at ON alerts(sent_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_external_trend_pair_time ON external_trend_metrics(pair_address, observed_at_hour DESC)",
    "CREATE INDEX IF NOT EXISTS idx_external_ohlcv_lookup ON external_ohlcv(network, pool_address, timeframe, aggregate, source, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_external_json_cache_fetched ON external_json_cache(fetched_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_signal_predictions_pair_time ON signal_predictions(pair_address, observed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_signal_predictions_maturity ON signal_predictions(observed_at)",
]
PREDICTION_OUTCOME_MATURITY_HOURS = 25

COMPACT_SIGNAL_FEATURE_KEYS = {
    "fdv",
    "h1_return_live",
    "h24_return_live",
    "liquidity_usd",
    "market_cap",
    "market_cap_bucket",
    "price_change_h1",
    "price_change_h24",
    "price_usd",
    "volume_h1",
    "volume_h24",
    "volume_to_liquidity_h1",
}


def _compress_text(value: str) -> bytes:
    return zlib.compress(value.encode("utf-8"), level=9)


def _decompress_text(value: bytes, compression: str) -> str:
    if compression != "zlib":
        raise ValueError(f"unsupported compression: {compression}")
    return zlib.decompress(value).decode("utf-8")


def _hour_bucket(value: str) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return value[:13] + ":00:00+00:00"
    return isoformat_utc(parsed.replace(minute=0, second=0, microsecond=0)) or value


def _compact_signal_features(raw: str, archived_at: datetime) -> str:
    features = json_loads(raw, {})
    if not isinstance(features, dict):
        features = {}
    compact = {key: features[key] for key in sorted(COMPACT_SIGNAL_FEATURE_KEYS) if key in features}
    compact["_history_compacted"] = True
    return json_dumps(compact)


class MonitorRepository:
    def __init__(self, database_path: str) -> None:
        ensure_parent_dir(database_path)
        self._conn = sqlite3.connect(database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self._conn.close()

    def initialize(self) -> None:
        for statement in SCHEMA_STATEMENTS:
            self._conn.execute(statement)
        self._ensure_schema_migrations()
        self._conn.commit()

    def _ensure_schema_migrations(self) -> None:
        self._ensure_column(
            "signal_prediction_outcomes",
            "outcome_source",
            "TEXT NOT NULL DEFAULT 'unknown'",
        )
        self._ensure_column(
            "signal_prediction_outcomes",
            "base_price_source",
            "TEXT NOT NULL DEFAULT 'unknown'",
        )
        self._ensure_column("signal_prediction_outcomes", "base_price_usd", "REAL")
        self._ensure_column("signal_prediction_outcomes", "gecko_base_close_usd", "REAL")
        self._ensure_column("signal_prediction_outcomes", "price_divergence_pct", "REAL")
        self._ensure_column(
            "signal_prediction_outcomes",
            "quality_flags_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        self._ensure_column("signal_predictions", "short_momentum_score", "INTEGER")
        self._ensure_column("signal_predictions", "continuation_score", "INTEGER")
        self._ensure_column("signal_predictions", "breakout_score", "INTEGER")

    def _ensure_column(self, table_name: str, column_name: str, definition: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {str(row["name"]) for row in rows}
        if column_name in existing_columns:
            return
        self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def get_cursor(self, key: str) -> int | None:
        row = self._conn.execute(
            "SELECT cursor_value FROM scan_cursors WHERE cursor_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return int(row["cursor_value"])

    def set_cursor(self, key: str, value: int) -> None:
        now = isoformat_utc(utcnow())
        self._conn.execute(
            """
            INSERT INTO scan_cursors(cursor_key, cursor_value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(cursor_key) DO UPDATE SET
                cursor_value = excluded.cursor_value,
                updated_at = excluded.updated_at
            """,
            (key, str(value), now),
        )
        self._conn.commit()

    def upsert_token(
        self,
        token_address: str,
        symbol: str | None,
        name: str | None,
        seen_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        seen_raw = isoformat_utc(seen_at)
        current = self._conn.execute(
            "SELECT metadata_json FROM tokens WHERE token_address = ?",
            (token_address,),
        ).fetchone()
        merged_metadata = json_loads(current["metadata_json"], {}) if current else {}
        merged_metadata.update(metadata or {})
        self._conn.execute(
            """
            INSERT INTO tokens(token_address, symbol, name, first_seen_at, last_seen_at, metadata_json)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_address) DO UPDATE SET
                symbol = COALESCE(excluded.symbol, tokens.symbol),
                name = COALESCE(excluded.name, tokens.name),
                last_seen_at = excluded.last_seen_at,
                metadata_json = excluded.metadata_json
            """,
            (
                token_address,
                symbol,
                name,
                seen_raw,
                seen_raw,
                json_dumps(merged_metadata),
            ),
        )
        self._conn.commit()

    def insert_discovered_pair(self, pair: DiscoveredPair, chain_id: str) -> bool:
        now = isoformat_utc(utcnow())
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO pairs(
                pair_address,
                chain_id,
                token_address,
                quote_token_address,
                quote_symbol,
                token0_address,
                token1_address,
                pair_created_at,
                discovered_at,
                first_seen_block,
                first_seen_tx_hash,
                first_seen_log_index,
                state,
                active,
                next_refresh_at,
                updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 1, ?, ?)
            """,
            (
                pair.pair_address,
                chain_id,
                pair.token_address,
                pair.quote_token_address,
                pair.quote_symbol,
                pair.token0_address,
                pair.token1_address,
                isoformat_utc(pair.pair_created_at),
                isoformat_utc(pair.discovered_at),
                pair.first_seen_block,
                pair.first_seen_tx_hash,
                pair.first_seen_log_index,
                isoformat_utc(pair.discovered_at),
                now,
            ),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def upsert_seed_pair(
        self,
        *,
        pair_address: str,
        chain_id: str,
        token_address: str,
        token_symbol: str | None,
        token_name: str | None,
        quote_token_address: str,
        quote_symbol: str,
        token0_address: str,
        token1_address: str,
        pair_created_at: datetime,
        discovered_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO pairs(
                pair_address,
                chain_id,
                token_address,
                token_symbol,
                token_name,
                quote_token_address,
                quote_symbol,
                token0_address,
                token1_address,
                pair_created_at,
                discovered_at,
                first_seen_block,
                first_seen_tx_hash,
                first_seen_log_index,
                state,
                active,
                metadata_json,
                next_refresh_at,
                updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'alpha_seed', 0, 'watching', 1, ?, ?, ?)
            ON CONFLICT(pair_address) DO UPDATE SET
                token_symbol = COALESCE(excluded.token_symbol, pairs.token_symbol),
                token_name = COALESCE(excluded.token_name, pairs.token_name),
                quote_token_address = excluded.quote_token_address,
                quote_symbol = excluded.quote_symbol,
                token0_address = excluded.token0_address,
                token1_address = excluded.token1_address,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                pair_address,
                chain_id,
                token_address,
                token_symbol,
                token_name,
                quote_token_address,
                quote_symbol,
                token0_address,
                token1_address,
                isoformat_utc(pair_created_at),
                isoformat_utc(discovered_at),
                json_dumps(metadata or {}),
                isoformat_utc(discovered_at),
                isoformat_utc(utcnow()),
            ),
        )
        self._conn.commit()

    def archive_old_pairs(self, max_pair_age_hours: int) -> int:
        cutoff = utcnow() - timedelta(hours=max_pair_age_hours)
        cursor = self._conn.execute(
            """
            UPDATE pairs
            SET active = 0, state = 'archived', next_refresh_at = NULL, updated_at = ?
            WHERE active = 1 AND pair_created_at < ?
            """,
            (isoformat_utc(utcnow()), isoformat_utc(cutoff)),
        )
        self._conn.commit()
        return cursor.rowcount

    def list_due_pairs(self, now: datetime, limit: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM pairs
            WHERE active = 1
              AND (next_refresh_at IS NULL OR next_refresh_at <= ?)
            ORDER BY
                CASE state
                    WHEN 'alerted' THEN 0
                    WHEN 'focused' THEN 1
                    WHEN 'watching' THEN 2
                    WHEN 'new' THEN 3
                    ELSE 4
                END,
                COALESCE(next_refresh_at, discovered_at) ASC
            LIMIT ?
            """,
            (isoformat_utc(now), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_due_pairs_for_tokens(self, token_addresses: list[str], now: datetime, limit: int) -> list[dict[str, Any]]:
        clean_addresses = sorted({address for address in token_addresses if address})
        if not clean_addresses:
            return []
        placeholders = ",".join("?" for _ in clean_addresses)
        rows = self._conn.execute(
            f"""
            SELECT *
            FROM pairs
            WHERE active = 1
              AND token_address IN ({placeholders})
              AND (next_refresh_at IS NULL OR next_refresh_at <= ?)
            ORDER BY
                CASE state
                    WHEN 'alerted' THEN 0
                    WHEN 'focused' THEN 1
                    WHEN 'watching' THEN 2
                    WHEN 'new' THEN 3
                    ELSE 4
                END,
                COALESCE(next_refresh_at, discovered_at) ASC
            LIMIT ?
            """,
            (*clean_addresses, isoformat_utc(now), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_alpha_tokens_needing_pair_seed(
        self,
        token_addresses: list[str],
        *,
        now: datetime,
        refresh_after: datetime,
        limit: int,
    ) -> list[str]:
        clean_addresses = sorted({address for address in token_addresses if address})
        if not clean_addresses:
            return []
        placeholders = ",".join("?" for _ in clean_addresses)
        rows = self._conn.execute(
            f"""
            SELECT t.token_address, t.metadata_json, p.pair_address
            FROM tokens t
            LEFT JOIN pairs p ON p.token_address = t.token_address
            WHERE t.token_address IN ({placeholders})
            ORDER BY t.token_address ASC
            """,
            clean_addresses,
        ).fetchall()
        due: list[str] = []
        seen: set[str] = set()
        for row in rows:
            token_address = row["token_address"]
            if token_address in seen:
                continue
            metadata = json_loads(row["metadata_json"], {})
            seeded_at = parse_datetime(metadata.get("pair_seeded_at"))
            failed_at = parse_datetime(metadata.get("pair_seed_failed_at"))
            has_pair = row["pair_address"] is not None
            needs_seed = False
            if not has_pair:
                needs_seed = failed_at is None or failed_at <= refresh_after
            elif seeded_at is None or seeded_at <= refresh_after:
                needs_seed = True
            elif failed_at is not None and failed_at <= refresh_after:
                needs_seed = True
            seen.add(token_address)
            if needs_seed:
                due.append(token_address)
            if len(due) >= limit:
                break
        return due

    def update_pair_after_snapshot(
        self,
        pair_address: str,
        *,
        state: str,
        dex_id: str | None,
        token_symbol: str | None,
        token_name: str | None,
        last_snapshot_at: datetime,
        next_refresh_at: datetime | None,
        risk_flags: list[str],
        metadata: dict[str, Any],
        active: bool = True,
    ) -> None:
        self._conn.execute(
            """
            UPDATE pairs
            SET state = ?,
                dex_id = COALESCE(?, dex_id),
                token_symbol = COALESCE(?, token_symbol),
                token_name = COALESCE(?, token_name),
                last_snapshot_at = ?,
                next_refresh_at = ?,
                risk_flags = ?,
                metadata_json = ?,
                active = ?,
                updated_at = ?
            WHERE pair_address = ?
            """,
            (
                state,
                dex_id,
                token_symbol,
                token_name,
                isoformat_utc(last_snapshot_at),
                isoformat_utc(next_refresh_at),
                json_dumps(risk_flags),
                json_dumps(metadata),
                1 if active else 0,
                isoformat_utc(utcnow()),
                pair_address,
            ),
        )
        self._conn.commit()

    def schedule_pair_retry(
        self,
        pair_address: str,
        next_refresh_at: datetime,
        reason: str,
        *,
        metadata_updates: dict[str, Any] | None = None,
    ) -> None:
        metadata_row = self._conn.execute(
            "SELECT metadata_json FROM pairs WHERE pair_address = ?",
            (pair_address,),
        ).fetchone()
        metadata = json_loads(metadata_row["metadata_json"] if metadata_row else None, {})
        metadata.update(metadata_updates or {})
        metadata["last_retry_reason"] = reason
        metadata["last_retry_scheduled_at"] = isoformat_utc(next_refresh_at)
        self._conn.execute(
            """
            UPDATE pairs
            SET next_refresh_at = ?, metadata_json = ?, updated_at = ?
            WHERE pair_address = ?
            """,
            (isoformat_utc(next_refresh_at), json_dumps(metadata), isoformat_utc(utcnow()), pair_address),
        )
        self._conn.commit()

    def insert_snapshot(self, snapshot: PairSnapshot, age_minutes: float, risk_flags: list[str]) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO snapshots(
                pair_address,
                token_address,
                observed_at,
                price_usd,
                price_native,
                liquidity_usd,
                fdv,
                market_cap,
                volume_m5,
                volume_h1,
                volume_h24,
                buys_m5,
                sells_m5,
                buys_h1,
                sells_h1,
                price_change_m5,
                price_change_h1,
                price_change_h24,
                website_count,
                social_count,
                boosts_active,
                age_minutes,
                risk_flags,
                raw_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.pair_address,
                snapshot.token_address,
                isoformat_utc(snapshot.observed_at),
                snapshot.price_usd,
                snapshot.price_native,
                snapshot.liquidity_usd,
                snapshot.fdv,
                snapshot.market_cap,
                snapshot.volume_m5,
                snapshot.volume_h1,
                snapshot.volume_h24,
                snapshot.buys_m5,
                snapshot.sells_m5,
                snapshot.buys_h1,
                snapshot.sells_h1,
                snapshot.price_change_m5,
                snapshot.price_change_h1,
                snapshot.price_change_h24,
                snapshot.website_count,
                snapshot.social_count,
                snapshot.boosts_active,
                age_minutes,
                json_dumps(risk_flags),
                json_dumps(snapshot.raw_payload),
            ),
        )
        self._conn.commit()

    def insert_signal(self, pair_address: str, token_address: str, decision: SignalDecision) -> int:
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO signals(
                pair_address,
                token_address,
                observed_at,
                strategy_version,
                score,
                pair_state,
                should_alert,
                reasons,
                risk_flags,
                feature_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pair_address,
                token_address,
                isoformat_utc(decision.observed_at),
                decision.strategy_version,
                decision.score,
                decision.pair_state,
                1 if decision.should_alert else 0,
                json_dumps(list(decision.reasons)),
                json_dumps(list(decision.risk_flags)),
                json_dumps(decision.features),
            ),
        )
        self._conn.execute(
            """
            UPDATE signals
            SET
                token_address = ?,
                score = ?,
                pair_state = ?,
                should_alert = ?,
                reasons = ?,
                risk_flags = ?,
                feature_json = ?
            WHERE pair_address = ? AND observed_at = ? AND strategy_version = ?
            """,
            (
                token_address,
                decision.score,
                decision.pair_state,
                1 if decision.should_alert else 0,
                json_dumps(list(decision.reasons)),
                json_dumps(list(decision.risk_flags)),
                json_dumps(decision.features),
                pair_address,
                isoformat_utc(decision.observed_at),
                decision.strategy_version,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            """
            SELECT id
            FROM signals
            WHERE pair_address = ? AND observed_at = ? AND strategy_version = ?
            """,
            (pair_address, isoformat_utc(decision.observed_at), decision.strategy_version),
        ).fetchone()
        if row is None:
            raise RuntimeError("failed to fetch signal row after insert")
        return int(row["id"])

    def estimate_history_compaction(self, before: datetime) -> dict[str, Any]:
        before_iso = isoformat_utc(before)
        snapshot_row = self._conn.execute(
            """
            SELECT
                COUNT(*) AS rows_count,
                COALESCE(SUM(LENGTH(raw_json)), 0) AS raw_bytes
            FROM snapshots
            WHERE observed_at < ? AND raw_json != '{}'
            """,
            (before_iso,),
        ).fetchone()
        signal_row = self._conn.execute(
            """
            SELECT
                COUNT(*) AS rows_count,
                COALESCE(SUM(LENGTH(feature_json)), 0) AS feature_bytes
            FROM signals
            WHERE observed_at < ? AND feature_json NOT LIKE '%"_history_compacted":true%'
            """,
            (before_iso,),
        ).fetchone()
        rollup_row = self._conn.execute(
            """
            SELECT COUNT(*) AS rows_count
            FROM (
                SELECT pair_address, SUBSTR(observed_at, 1, 13) AS observed_hour
                FROM snapshots
                WHERE observed_at < ? AND raw_json != '{}'
                GROUP BY pair_address, observed_hour
            )
            """,
            (before_iso,),
        ).fetchone()
        return {
            "before": before_iso,
            "snapshot_rows": int(snapshot_row["rows_count"]) if snapshot_row else 0,
            "snapshot_raw_json_bytes": int(snapshot_row["raw_bytes"]) if snapshot_row else 0,
            "signal_rows": int(signal_row["rows_count"]) if signal_row else 0,
            "signal_feature_json_bytes": int(signal_row["feature_bytes"]) if signal_row else 0,
            "snapshot_hourly_rollup_rows": int(rollup_row["rows_count"]) if rollup_row else 0,
        }

    def compact_history(self, before: datetime, *, batch_size: int = 5000) -> dict[str, Any]:
        before_iso = isoformat_utc(before)
        if before_iso is None:
            raise ValueError("before must be a valid datetime")
        archived_at = utcnow()
        self._upsert_snapshot_hourly_rollups(before_iso, archived_at)
        snapshot_rows = self._archive_snapshot_raw_json(before_iso, archived_at, batch_size=batch_size)
        signal_rows = self._archive_signal_features(before_iso, archived_at, batch_size=batch_size)
        signal_recompacted_rows = self._recompact_archived_signal_features(before_iso, archived_at, batch_size=batch_size)
        rollup_count = self._conn.execute(
            "SELECT COUNT(*) AS n FROM snapshot_hourly_rollups WHERE observed_at_hour < ?",
            (before_iso,),
        ).fetchone()
        self._conn.commit()
        return {
            "before": before_iso,
            "snapshot_rows_compacted": snapshot_rows,
            "signal_rows_compacted": signal_rows,
            "signal_rows_recompacted": signal_recompacted_rows,
            "snapshot_hourly_rollup_rows": int(rollup_count["n"]) if rollup_count else 0,
        }

    def _upsert_snapshot_hourly_rollups(self, before_iso: str, now: datetime) -> None:
        rows = self._conn.execute(
            """
            SELECT
                pair_address,
                token_address,
                observed_at,
                price_usd,
                liquidity_usd,
                volume_m5,
                volume_h1,
                volume_h24,
                buys_m5,
                sells_m5
            FROM snapshots
            WHERE observed_at < ? AND raw_json != '{}'
            ORDER BY pair_address ASC, observed_at ASC
            """,
            (before_iso,),
        )
        rollups: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            hour = _hour_bucket(row["observed_at"])
            key = (row["pair_address"], hour)
            price = row["price_usd"]
            liquidity = row["liquidity_usd"]
            rollup = rollups.get(key)
            if rollup is None:
                rollup = {
                    "pair_address": row["pair_address"],
                    "token_address": row["token_address"],
                    "observed_at_hour": hour,
                    "first_observed_at": row["observed_at"],
                    "last_observed_at": row["observed_at"],
                    "sample_count": 0,
                    "open_price_usd": price,
                    "high_price_usd": price,
                    "low_price_usd": price,
                    "close_price_usd": price,
                    "liquidity_sum": 0.0,
                    "liquidity_samples": 0,
                    "max_liquidity_usd": liquidity,
                    "max_volume_h1": row["volume_h1"],
                    "max_volume_h24": row["volume_h24"],
                    "sum_volume_m5": 0.0,
                    "sum_buys_m5": 0,
                    "sum_sells_m5": 0,
                }
                rollups[key] = rollup
            rollup["sample_count"] += 1
            rollup["last_observed_at"] = row["observed_at"]
            rollup["close_price_usd"] = price
            if price is not None:
                rollup["high_price_usd"] = price if rollup["high_price_usd"] is None else max(rollup["high_price_usd"], price)
                rollup["low_price_usd"] = price if rollup["low_price_usd"] is None else min(rollup["low_price_usd"], price)
            if liquidity is not None:
                rollup["liquidity_sum"] += float(liquidity)
                rollup["liquidity_samples"] += 1
                rollup["max_liquidity_usd"] = (
                    liquidity if rollup["max_liquidity_usd"] is None else max(rollup["max_liquidity_usd"], liquidity)
                )
            if row["volume_h1"] is not None:
                rollup["max_volume_h1"] = row["volume_h1"] if rollup["max_volume_h1"] is None else max(rollup["max_volume_h1"], row["volume_h1"])
            if row["volume_h24"] is not None:
                rollup["max_volume_h24"] = row["volume_h24"] if rollup["max_volume_h24"] is None else max(rollup["max_volume_h24"], row["volume_h24"])
            rollup["sum_volume_m5"] += float(row["volume_m5"] or 0)
            rollup["sum_buys_m5"] += int(row["buys_m5"] or 0)
            rollup["sum_sells_m5"] += int(row["sells_m5"] or 0)

        now_iso = isoformat_utc(now)
        for rollup in rollups.values():
            avg_liquidity = None
            if rollup["liquidity_samples"]:
                avg_liquidity = rollup["liquidity_sum"] / rollup["liquidity_samples"]
            self._conn.execute(
                """
                INSERT INTO snapshot_hourly_rollups(
                    pair_address,
                    token_address,
                    observed_at_hour,
                    first_observed_at,
                    last_observed_at,
                    sample_count,
                    open_price_usd,
                    high_price_usd,
                    low_price_usd,
                    close_price_usd,
                    avg_liquidity_usd,
                    max_liquidity_usd,
                    max_volume_h1,
                    max_volume_h24,
                    sum_volume_m5,
                    sum_buys_m5,
                    sum_sells_m5,
                    created_at,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pair_address, observed_at_hour) DO UPDATE SET
                    token_address = excluded.token_address,
                    first_observed_at = excluded.first_observed_at,
                    last_observed_at = excluded.last_observed_at,
                    sample_count = excluded.sample_count,
                    open_price_usd = excluded.open_price_usd,
                    high_price_usd = excluded.high_price_usd,
                    low_price_usd = excluded.low_price_usd,
                    close_price_usd = excluded.close_price_usd,
                    avg_liquidity_usd = excluded.avg_liquidity_usd,
                    max_liquidity_usd = excluded.max_liquidity_usd,
                    max_volume_h1 = excluded.max_volume_h1,
                    max_volume_h24 = excluded.max_volume_h24,
                    sum_volume_m5 = excluded.sum_volume_m5,
                    sum_buys_m5 = excluded.sum_buys_m5,
                    sum_sells_m5 = excluded.sum_sells_m5,
                    updated_at = excluded.updated_at
                """,
                (
                    rollup["pair_address"],
                    rollup["token_address"],
                    rollup["observed_at_hour"],
                    rollup["first_observed_at"],
                    rollup["last_observed_at"],
                    rollup["sample_count"],
                    rollup["open_price_usd"],
                    rollup["high_price_usd"],
                    rollup["low_price_usd"],
                    rollup["close_price_usd"],
                    avg_liquidity,
                    rollup["max_liquidity_usd"],
                    rollup["max_volume_h1"],
                    rollup["max_volume_h24"],
                    rollup["sum_volume_m5"],
                    rollup["sum_buys_m5"],
                    rollup["sum_sells_m5"],
                    now_iso,
                    now_iso,
                ),
            )

    def _archive_snapshot_raw_json(self, before_iso: str, archived_at: datetime, *, batch_size: int) -> int:
        archived_at_iso = isoformat_utc(archived_at)
        total = 0
        while True:
            rows = self._conn.execute(
                """
                SELECT id, raw_json
                FROM snapshots
                WHERE observed_at < ? AND raw_json != '{}'
                ORDER BY observed_at ASC
                LIMIT ?
                """,
                (before_iso, batch_size),
            ).fetchall()
            if not rows:
                return total
            for row in rows:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO snapshot_raw_archives(snapshot_id, archived_at, compression, raw_json_z)
                    VALUES(?, ?, 'zlib', ?)
                    """,
                    (int(row["id"]), archived_at_iso, _compress_text(row["raw_json"])),
                )
                self._conn.execute("UPDATE snapshots SET raw_json = '{}' WHERE id = ?", (int(row["id"]),))
                total += 1

    def _archive_signal_features(self, before_iso: str, archived_at: datetime, *, batch_size: int) -> int:
        archived_at_iso = isoformat_utc(archived_at)
        total = 0
        while True:
            rows = self._conn.execute(
                """
                SELECT id, feature_json
                FROM signals
                WHERE observed_at < ? AND feature_json NOT LIKE '%"_history_compacted":true%'
                ORDER BY observed_at ASC
                LIMIT ?
                """,
                (before_iso, batch_size),
            ).fetchall()
            if not rows:
                return total
            for row in rows:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO signal_feature_archives(signal_id, archived_at, compression, feature_json_z)
                    VALUES(?, ?, 'zlib', ?)
                    """,
                    (int(row["id"]), archived_at_iso, _compress_text(row["feature_json"])),
                )
                self._conn.execute(
                    "UPDATE signals SET feature_json = ? WHERE id = ?",
                    (_compact_signal_features(row["feature_json"], archived_at), int(row["id"])),
                )
                total += 1

    def _recompact_archived_signal_features(self, before_iso: str, archived_at: datetime, *, batch_size: int) -> int:
        total = 0
        offset = 0
        while True:
            rows = self._conn.execute(
                """
                SELECT s.id, s.feature_json, a.compression, a.feature_json_z
                FROM signals s
                JOIN signal_feature_archives a ON a.signal_id = s.id
                WHERE s.observed_at < ? AND s.feature_json LIKE '%"_history_compacted":true%'
                ORDER BY s.observed_at ASC
                LIMIT ? OFFSET ?
                """,
                (before_iso, batch_size, offset),
            ).fetchall()
            if not rows:
                return total
            for row in rows:
                full_feature_json = _decompress_text(row["feature_json_z"], row["compression"])
                compact_feature_json = _compact_signal_features(full_feature_json, archived_at)
                if compact_feature_json == row["feature_json"]:
                    continue
                self._conn.execute(
                    "UPDATE signals SET feature_json = ? WHERE id = ?",
                    (compact_feature_json, int(row["id"])),
                )
                total += 1
            offset += batch_size

    def upsert_signal_prediction(
        self,
        signal_id: int,
        *,
        pair_address: str,
        token_address: str,
        observed_at: datetime,
        prediction: PredictionResult,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO signal_predictions(
                signal_id,
                pair_address,
                token_address,
                observed_at,
                predictor_version,
                prob_2h_up20,
                prob_6h_up50,
                prob_24h_up100,
                risk_6h_dd30,
                opportunity_score,
                short_momentum_score,
                continuation_score,
                breakout_score,
                stage,
                reasons_json,
                created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                pair_address = excluded.pair_address,
                token_address = excluded.token_address,
                observed_at = excluded.observed_at,
                predictor_version = excluded.predictor_version,
                prob_2h_up20 = excluded.prob_2h_up20,
                prob_6h_up50 = excluded.prob_6h_up50,
                prob_24h_up100 = excluded.prob_24h_up100,
                risk_6h_dd30 = excluded.risk_6h_dd30,
                opportunity_score = excluded.opportunity_score,
                short_momentum_score = excluded.short_momentum_score,
                continuation_score = excluded.continuation_score,
                breakout_score = excluded.breakout_score,
                stage = excluded.stage,
                reasons_json = excluded.reasons_json,
                created_at = excluded.created_at
            """,
            (
                signal_id,
                pair_address,
                token_address,
                isoformat_utc(observed_at),
                prediction.predictor_version,
                prediction.prob_2h_up20,
                prediction.prob_6h_up50,
                prediction.prob_24h_up100,
                prediction.risk_6h_dd30,
                prediction.opportunity_score,
                prediction.short_momentum_score,
                prediction.continuation_score,
                prediction.breakout_score,
                prediction.stage,
                json_dumps(list(prediction.reasons)),
                isoformat_utc(utcnow()),
            ),
        )
        self._conn.commit()

    def list_predictions_needing_outcomes(
        self,
        now: datetime,
        limit: int = 100,
        *,
        include_missing_quality: bool = False,
    ) -> list[dict[str, Any]]:
        mature_before = now - timedelta(hours=PREDICTION_OUTCOME_MATURITY_HOURS)
        outcome_filter = "outcome.signal_id IS NULL"
        if include_missing_quality:
            outcome_filter = """
                (
                    outcome.signal_id IS NULL
                    OR outcome.outcome_source = 'unknown'
                    OR outcome.base_price_source = 'unknown'
                )
            """
        rows = self._conn.execute(
            f"""
            SELECT pred.*, s.feature_json
            FROM signal_predictions pred
            JOIN signals s ON s.id = pred.signal_id
            LEFT JOIN signal_prediction_outcomes outcome ON outcome.signal_id = pred.signal_id
            WHERE {outcome_filter}
              AND pred.observed_at <= ?
            ORDER BY pred.observed_at ASC
            LIMIT ?
            """,
            (isoformat_utc(mature_before), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def compute_prediction_outcome(self, pair_address: str, observed_at: datetime) -> dict[str, Any]:
        rows = self._conn.execute(
            """
            SELECT observed_at, price_usd
            FROM snapshots
            WHERE pair_address = ?
              AND observed_at >= ?
              AND observed_at <= ?
              AND price_usd IS NOT NULL
              AND price_usd > 0
            ORDER BY observed_at ASC
            """,
            (
                pair_address,
                isoformat_utc(observed_at),
                isoformat_utc(observed_at + timedelta(hours=24)),
            ),
        ).fetchall()
        prices: list[tuple[datetime, float]] = []
        for row in rows:
            dt = parse_datetime(row["observed_at"])
            if dt is None:
                continue
            prices.append((dt, float(row["price_usd"])))
        base = next((price for dt, price in prices if dt >= observed_at and price > 0), None)
        if base is None:
            return {
                "outcome_source": "local_snapshots",
                "base_price_source": "missing",
                "base_price_usd": None,
                "gecko_base_close_usd": None,
                "price_divergence_pct": None,
                "quality_flags": ["base_price_missing", "local_snapshot_fallback"],
                "max_return_2h": None,
                "max_return_6h": None,
                "max_return_24h": None,
                "min_return_6h": None,
                "hit_2h_up20": 0,
                "hit_6h_up50": 0,
                "hit_24h_up100": 0,
                "hit_6h_dd30": 0,
                "sample_count_2h": 0,
                "sample_count_6h": 0,
                "sample_count_24h": 0,
            }
        future_rows = [(dt, price) for dt, price in prices if dt > observed_at]

        def window(hours: int) -> list[float]:
            cutoff = observed_at + timedelta(hours=hours)
            return [price for dt, price in future_rows if dt <= cutoff]

        def max_return(values: list[float]) -> float | None:
            return max(values) / base - 1 if values else None

        def min_return(values: list[float]) -> float | None:
            return min(values) / base - 1 if values else None

        values_2h = window(2)
        values_6h = window(6)
        values_24h = window(24)
        max_return_2h = max_return(values_2h)
        max_return_6h = max_return(values_6h)
        max_return_24h = max_return(values_24h)
        min_return_6h = min_return(values_6h)
        quality_flags = ["local_snapshot_fallback"]
        if len(values_2h) < 2:
            quality_flags.append("partial_2h_snapshots")
        if len(values_6h) < 5:
            quality_flags.append("partial_6h_snapshots")
        if len(values_24h) < 18:
            quality_flags.append("partial_24h_snapshots")
        return {
            "outcome_source": "local_snapshots",
            "base_price_source": "local_snapshot_price",
            "base_price_usd": base,
            "gecko_base_close_usd": None,
            "price_divergence_pct": None,
            "quality_flags": quality_flags,
            "max_return_2h": max_return_2h,
            "max_return_6h": max_return_6h,
            "max_return_24h": max_return_24h,
            "min_return_6h": min_return_6h,
            "hit_2h_up20": 1 if max_return_2h is not None and max_return_2h >= 0.20 else 0,
            "hit_6h_up50": 1 if max_return_6h is not None and max_return_6h >= 0.50 else 0,
            "hit_24h_up100": 1 if max_return_24h is not None and max_return_24h >= 1.00 else 0,
            "hit_6h_dd30": 1 if min_return_6h is not None and min_return_6h <= -0.30 else 0,
            "sample_count_2h": len(values_2h),
            "sample_count_6h": len(values_6h),
            "sample_count_24h": len(values_24h),
        }

    def upsert_prediction_outcome(self, signal_id: int, outcome: dict[str, Any], *, evaluated_at: datetime | None = None) -> None:
        self._conn.execute(
            """
            INSERT INTO signal_prediction_outcomes(
                signal_id,
                evaluated_at,
                outcome_source,
                base_price_source,
                base_price_usd,
                gecko_base_close_usd,
                price_divergence_pct,
                quality_flags_json,
                max_return_2h,
                max_return_6h,
                max_return_24h,
                min_return_6h,
                hit_2h_up20,
                hit_6h_up50,
                hit_24h_up100,
                hit_6h_dd30,
                sample_count_2h,
                sample_count_6h,
                sample_count_24h
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                evaluated_at = excluded.evaluated_at,
                outcome_source = excluded.outcome_source,
                base_price_source = excluded.base_price_source,
                base_price_usd = excluded.base_price_usd,
                gecko_base_close_usd = excluded.gecko_base_close_usd,
                price_divergence_pct = excluded.price_divergence_pct,
                quality_flags_json = excluded.quality_flags_json,
                max_return_2h = excluded.max_return_2h,
                max_return_6h = excluded.max_return_6h,
                max_return_24h = excluded.max_return_24h,
                min_return_6h = excluded.min_return_6h,
                hit_2h_up20 = excluded.hit_2h_up20,
                hit_6h_up50 = excluded.hit_6h_up50,
                hit_24h_up100 = excluded.hit_24h_up100,
                hit_6h_dd30 = excluded.hit_6h_dd30,
                sample_count_2h = excluded.sample_count_2h,
                sample_count_6h = excluded.sample_count_6h,
                sample_count_24h = excluded.sample_count_24h
            """,
            (
                signal_id,
                isoformat_utc(evaluated_at or utcnow()),
                outcome.get("outcome_source") or "unknown",
                outcome.get("base_price_source") or "unknown",
                outcome.get("base_price_usd"),
                outcome.get("gecko_base_close_usd"),
                outcome.get("price_divergence_pct"),
                outcome.get("quality_flags_json") or json_dumps(list(outcome.get("quality_flags") or [])),
                outcome.get("max_return_2h"),
                outcome.get("max_return_6h"),
                outcome.get("max_return_24h"),
                outcome.get("min_return_6h"),
                int(outcome.get("hit_2h_up20") or 0),
                int(outcome.get("hit_6h_up50") or 0),
                int(outcome.get("hit_24h_up100") or 0),
                int(outcome.get("hit_6h_dd30") or 0),
                int(outcome.get("sample_count_2h") or 0),
                int(outcome.get("sample_count_6h") or 0),
                int(outcome.get("sample_count_24h") or 0),
            ),
        )
        self._conn.commit()

    def get_recent_successful_alert_at(self, pair_address: str, channel: str) -> datetime | None:
        row = self._conn.execute(
            """
            SELECT a.sent_at
            FROM alerts a
            JOIN signals s ON s.id = a.signal_id
            WHERE s.pair_address = ? AND a.channel = ? AND a.delivery_state = 'sent'
            ORDER BY a.sent_at DESC
            LIMIT 1
            """,
            (pair_address, channel),
        ).fetchone()
        if row is None:
            return None
        return parse_datetime(row["sent_at"])

    def record_alert(
        self,
        signal_id: int,
        *,
        channel: str,
        delivery_state: str,
        provider_message_id: str | None = None,
        error_text: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO alerts(
                signal_id,
                channel,
                sent_at,
                delivery_state,
                provider_message_id,
                error_text
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                channel,
                isoformat_utc(utcnow()),
                delivery_state,
                provider_message_id,
                error_text,
            ),
        )
        self._conn.commit()

    def get_signal_row(self, signal_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_tokens_for_cleanup(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT token_address, symbol, name, metadata_json FROM tokens",
        ).fetchall()
        return [dict(row) for row in rows]

    def update_token_metadata(self, token_address: str, metadata: dict[str, Any]) -> None:
        self._conn.execute(
            "UPDATE tokens SET metadata_json = ?, last_seen_at = ? WHERE token_address = ?",
            (json_dumps(metadata), isoformat_utc(utcnow()), token_address),
        )
        self._conn.commit()

    def get_token_metadata(self, token_address: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT metadata_json FROM tokens WHERE token_address = ?",
            (token_address,),
        ).fetchone()
        return json_loads(row["metadata_json"], {}) if row else {}

    def list_tokens_needing_holder_metrics(
        self,
        *,
        stale_before: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT
                t.token_address,
                t.symbol,
                t.name,
                t.metadata_json,
                MAX(p.last_snapshot_at) AS last_snapshot_at
            FROM tokens t
            JOIN pairs p ON p.token_address = t.token_address
            WHERE p.active = 1
            GROUP BY t.token_address
            ORDER BY COALESCE(MAX(p.last_snapshot_at), t.last_seen_at) DESC
            """
        ).fetchall()
        due: list[dict[str, Any]] = []
        for row in rows:
            metadata = json_loads(row["metadata_json"], {})
            last_attempt = parse_datetime(
                metadata.get("holder_metrics_attempted_at") or metadata.get("holder_metrics_updated_at")
            )
            stale = last_attempt is None or last_attempt <= stale_before
            if stale:
                due.append(
                    {
                        "token_address": row["token_address"],
                        "symbol": row["symbol"],
                        "name": row["name"],
                        "metadata": metadata,
                    }
                )
            if len(due) >= limit:
                break
        return due

    def list_snapshots_for_cleanup(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT
                s.*,
                p.token_symbol,
                p.token_name,
                p.quote_token_address,
                p.quote_symbol,
                p.pair_created_at,
                p.dex_id,
                p.metadata_json AS pair_metadata_json,
                t.metadata_json AS token_metadata_json
            FROM snapshots s
            JOIN pairs p ON p.pair_address = s.pair_address
            JOIN tokens t ON t.token_address = s.token_address
            ORDER BY s.observed_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def update_snapshot_cleaned(self, snapshot_id: int, *, snapshot: PairSnapshot, age_minutes: float, risk_flags: list[str]) -> None:
        self._conn.execute(
            """
            UPDATE snapshots
            SET
                price_usd = ?,
                price_native = ?,
                liquidity_usd = ?,
                fdv = ?,
                market_cap = ?,
                volume_m5 = ?,
                volume_h1 = ?,
                volume_h24 = ?,
                buys_m5 = ?,
                sells_m5 = ?,
                buys_h1 = ?,
                sells_h1 = ?,
                price_change_m5 = ?,
                price_change_h1 = ?,
                price_change_h24 = ?,
                website_count = ?,
                social_count = ?,
                boosts_active = ?,
                age_minutes = ?,
                risk_flags = ?,
                raw_json = ?
            WHERE id = ?
            """,
            (
                snapshot.price_usd,
                snapshot.price_native,
                snapshot.liquidity_usd,
                snapshot.fdv,
                snapshot.market_cap,
                snapshot.volume_m5,
                snapshot.volume_h1,
                snapshot.volume_h24,
                snapshot.buys_m5,
                snapshot.sells_m5,
                snapshot.buys_h1,
                snapshot.sells_h1,
                snapshot.price_change_m5,
                snapshot.price_change_h1,
                snapshot.price_change_h24,
                snapshot.website_count,
                snapshot.social_count,
                snapshot.boosts_active,
                age_minutes,
                json_dumps(risk_flags),
                json_dumps(snapshot.raw_payload),
                snapshot_id,
            ),
        )
        self._conn.commit()

    def list_pair_overview(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT
                p.pair_address,
                p.token_address,
                COALESCE(p.token_symbol, t.symbol, substr(p.token_address, 1, 8)) AS token_symbol,
                COALESCE(p.token_name, t.name) AS token_name,
                t.metadata_json AS token_metadata_json,
                p.quote_symbol,
                p.dex_id,
                p.state,
                p.active,
                p.pair_created_at,
                p.last_snapshot_at,
                p.risk_flags,
                snap.observed_at AS snapshot_observed_at,
                snap.price_usd,
                snap.liquidity_usd,
                snap.market_cap,
                snap.fdv,
                snap.volume_h1,
                snap.volume_m5,
                s.observed_at AS last_signal_at,
                s.score AS last_score,
                s.pair_state AS last_pair_state,
                s.should_alert AS last_should_alert,
                s.reasons AS last_reasons,
                s.risk_flags AS last_risk_flags,
                s.feature_json AS last_feature_json,
                pred.prob_2h_up20 AS prediction_prob_2h_up20,
                pred.prob_6h_up50 AS prediction_prob_6h_up50,
                pred.prob_24h_up100 AS prediction_prob_24h_up100,
                pred.risk_6h_dd30 AS prediction_risk_6h_dd30,
                pred.opportunity_score AS prediction_opportunity_score,
                COALESCE(pred.short_momentum_score, pred.opportunity_score) AS prediction_short_momentum_score,
                pred.continuation_score AS prediction_continuation_score,
                pred.breakout_score AS prediction_breakout_score,
                pred.stage AS prediction_stage,
                pred.reasons_json AS prediction_reasons
            FROM pairs p
            LEFT JOIN tokens t ON t.token_address = p.token_address
            LEFT JOIN snapshots snap ON snap.id = (
                SELECT snap2.id FROM snapshots snap2
                WHERE snap2.pair_address = p.pair_address
                ORDER BY snap2.observed_at DESC
                LIMIT 1
            )
            LEFT JOIN signals s ON s.id = (
                SELECT s2.id FROM signals s2
                WHERE s2.pair_address = p.pair_address
                ORDER BY s2.observed_at DESC
                LIMIT 1
            )
            LEFT JOIN signal_predictions pred ON pred.signal_id = s.id
            ORDER BY
                CASE
                    WHEN json_extract(t.metadata_json, '$.is_binance_alpha') IN (1, 'true') THEN 1
                    ELSE 0
                END DESC,
                p.active DESC,
                snap.observed_at IS NOT NULL DESC,
                snap.observed_at DESC,
                COALESCE(pred.short_momentum_score, pred.opportunity_score, 0) DESC,
                COALESCE(s.score, 0) DESC,
                p.discovered_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT
                a.sent_at,
                a.delivery_state,
                a.error_text,
                s.score,
                s.reasons,
                s.risk_flags,
                s.pair_address,
                s.token_address
            FROM alerts a
            JOIN signals s ON s.id = a.signal_id
            ORDER BY a.sent_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_pair_detail(self, pair_address: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT *
            FROM pairs
            WHERE pair_address = ?
            """,
            (pair_address,),
        ).fetchone()
        return dict(row) if row else None

    def list_recent_signals(self, pair_address: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT
                s.*,
                pred.prob_2h_up20 AS prediction_prob_2h_up20,
                pred.prob_6h_up50 AS prediction_prob_6h_up50,
                pred.prob_24h_up100 AS prediction_prob_24h_up100,
                pred.risk_6h_dd30 AS prediction_risk_6h_dd30,
                pred.opportunity_score AS prediction_opportunity_score,
                COALESCE(pred.short_momentum_score, pred.opportunity_score) AS prediction_short_momentum_score,
                pred.continuation_score AS prediction_continuation_score,
                pred.breakout_score AS prediction_breakout_score,
                pred.stage AS prediction_stage,
                pred.reasons_json AS prediction_reasons,
                outcome.max_return_2h AS outcome_max_return_2h,
                outcome.max_return_6h AS outcome_max_return_6h,
                outcome.max_return_24h AS outcome_max_return_24h,
                outcome.min_return_6h AS outcome_min_return_6h,
                outcome.hit_2h_up20 AS outcome_hit_2h_up20,
                outcome.hit_6h_up50 AS outcome_hit_6h_up50,
                outcome.hit_24h_up100 AS outcome_hit_24h_up100,
                outcome.hit_6h_dd30 AS outcome_hit_6h_dd30,
                outcome.sample_count_24h AS outcome_sample_count_24h
            FROM signals s
            LEFT JOIN signal_predictions pred ON pred.signal_id = s.id
            LEFT JOIN signal_prediction_outcomes outcome ON outcome.signal_id = s.id
            WHERE s.pair_address = ?
            ORDER BY s.observed_at DESC
            LIMIT ?
            """,
            (pair_address, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_snapshots(self, pair_address: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT observed_at, price_usd, liquidity_usd, volume_m5, volume_h1, buys_m5, sells_m5, age_minutes
            FROM snapshots
            WHERE pair_address = ?
            ORDER BY observed_at DESC
            LIMIT ?
            """,
            (pair_address, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_prediction_dataset_rows(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit_sql = ""
        params: tuple[Any, ...] = ()
        if limit is not None:
            limit_sql = "LIMIT ?"
            params = (limit,)
        rows = self._conn.execute(
            f"""
            SELECT
                s.id AS signal_id,
                s.pair_address,
                s.token_address,
                s.observed_at,
                s.score,
                s.strategy_version,
                s.pair_state,
                s.should_alert,
                s.reasons,
                s.risk_flags,
                s.feature_json,
                pred.predictor_version,
                pred.prob_2h_up20,
                pred.prob_6h_up50,
                pred.prob_24h_up100,
                pred.risk_6h_dd30,
                pred.opportunity_score,
                COALESCE(pred.short_momentum_score, pred.opportunity_score) AS short_momentum_score,
                pred.continuation_score,
                pred.breakout_score,
                pred.stage,
                pred.reasons_json AS prediction_reasons,
                outcome.max_return_2h,
                outcome.max_return_6h,
                outcome.max_return_24h,
                outcome.min_return_6h,
                outcome.hit_2h_up20,
                outcome.hit_6h_up50,
                outcome.hit_24h_up100,
                outcome.hit_6h_dd30,
                outcome.sample_count_2h,
                outcome.sample_count_6h,
                outcome.sample_count_24h,
                outcome.outcome_source,
                outcome.base_price_source,
                outcome.base_price_usd,
                outcome.gecko_base_close_usd,
                outcome.price_divergence_pct,
                outcome.quality_flags_json,
                t.symbol AS token_symbol,
                t.name AS token_name,
                t.metadata_json AS token_metadata_json,
                archive.compression AS archived_feature_compression,
                archive.feature_json_z AS archived_feature_json_z
            FROM signals s
            LEFT JOIN signal_predictions pred ON pred.signal_id = s.id
            LEFT JOIN signal_prediction_outcomes outcome ON outcome.signal_id = s.id
            LEFT JOIN tokens t ON t.token_address = s.token_address
            LEFT JOIN signal_feature_archives archive ON archive.signal_id = s.id
            WHERE pred.signal_id IS NOT NULL
            ORDER BY s.observed_at ASC
            {limit_sql}
            """,
            params,
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            archived_feature_json = item.pop("archived_feature_json_z", None)
            archived_feature_compression = item.pop("archived_feature_compression", None)
            if archived_feature_json is not None and archived_feature_compression:
                item["feature_json"] = _decompress_text(archived_feature_json, archived_feature_compression)
            result.append(item)
        return result

    def get_external_trend_metrics(
        self,
        pair_address: str,
        observed_at_hour: str,
        *,
        source: str = "geckoterminal",
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT pair_address, observed_at_hour, source, fetched_at, external_return_2h, external_return_24h, raw_json
            FROM external_trend_metrics
            WHERE pair_address = ? AND observed_at_hour = ? AND source = ?
            """,
            (pair_address, observed_at_hour, source),
        ).fetchone()
        return dict(row) if row else None

    def list_external_trend_metrics(
        self,
        pair_address: str,
        observed_at_hours: list[str],
        *,
        source: str = "geckoterminal",
    ) -> list[dict[str, Any]]:
        observed_at_hours = [value for value in dict.fromkeys(observed_at_hours) if value]
        if not pair_address or not observed_at_hours:
            return []
        placeholders = ", ".join("?" for _ in observed_at_hours)
        rows = self._conn.execute(
            f"""
            SELECT pair_address, observed_at_hour, source, fetched_at, external_return_2h, external_return_24h, raw_json
            FROM external_trend_metrics
            WHERE pair_address = ?
              AND source = ?
              AND observed_at_hour IN ({placeholders})
            """,
            (pair_address, source, *observed_at_hours),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_external_trend_metrics(
        self,
        pair_address: str,
        observed_at_hour: str,
        *,
        external_return_2h: float | None,
        external_return_24h: float | None,
        source: str = "geckoterminal",
        fetched_at: datetime | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO external_trend_metrics(
                pair_address,
                observed_at_hour,
                source,
                fetched_at,
                external_return_2h,
                external_return_24h,
                raw_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pair_address, observed_at_hour, source) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                external_return_2h = excluded.external_return_2h,
                external_return_24h = excluded.external_return_24h,
                raw_json = excluded.raw_json
            """,
            (
                pair_address,
                observed_at_hour,
                source,
                isoformat_utc(fetched_at or utcnow()),
                external_return_2h,
                external_return_24h,
                json_dumps(raw_payload or {}),
            ),
        )
        self._conn.commit()

    def list_external_ohlcv(
        self,
        *,
        network: str,
        pool_address: str,
        timeframe: str,
        aggregate: int,
        before_timestamp: int | None = None,
        limit: int | None = None,
        source: str = "geckoterminal",
    ) -> list[dict[str, Any]]:
        where = """
            network = ?
            AND pool_address = ?
            AND timeframe = ?
            AND aggregate = ?
            AND source = ?
        """
        params: list[Any] = [network, pool_address, timeframe, aggregate, source]
        if before_timestamp is not None:
            where += " AND ts < ?"
            params.append(before_timestamp)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(
            f"""
            SELECT ts, open, high, low, close, volume
            FROM external_ohlcv
            WHERE {where}
            ORDER BY ts DESC
            {limit_sql}
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def upsert_external_ohlcv(
        self,
        *,
        network: str,
        pool_address: str,
        timeframe: str,
        aggregate: int,
        rows: list[dict[str, float]],
        source: str = "geckoterminal",
        fetched_at: datetime | None = None,
    ) -> None:
        if not rows:
            return
        fetched_raw = isoformat_utc(fetched_at or utcnow())
        self._conn.executemany(
            """
            INSERT INTO external_ohlcv(
                network,
                pool_address,
                timeframe,
                aggregate,
                source,
                ts,
                open,
                high,
                low,
                close,
                volume,
                fetched_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, pool_address, timeframe, aggregate, source, ts) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                fetched_at = excluded.fetched_at
            """,
            [
                (
                    network,
                    pool_address,
                    timeframe,
                    aggregate,
                    source,
                    int(row["ts"]),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["volume"]),
                    fetched_raw,
                )
                for row in rows
            ],
        )
        self._conn.commit()

    def get_external_ohlcv_fetch_row_count(
        self,
        *,
        network: str,
        pool_address: str,
        timeframe: str,
        aggregate: int,
        limit: int,
        before_timestamp: int | None,
        source: str = "geckoterminal",
    ) -> int | None:
        row = self._conn.execute(
            """
            SELECT row_count
            FROM external_ohlcv_fetches
            WHERE network = ?
              AND pool_address = ?
              AND timeframe = ?
              AND aggregate = ?
              AND source = ?
              AND limit_count = ?
              AND before_timestamp = ?
            """,
            (network, pool_address, timeframe, aggregate, source, limit, before_timestamp if before_timestamp is not None else -1),
        ).fetchone()
        return int(row["row_count"]) if row else None

    def record_external_ohlcv_fetch(
        self,
        *,
        network: str,
        pool_address: str,
        timeframe: str,
        aggregate: int,
        limit: int,
        before_timestamp: int | None,
        row_count: int,
        source: str = "geckoterminal",
        fetched_at: datetime | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO external_ohlcv_fetches(
                network,
                pool_address,
                timeframe,
                aggregate,
                source,
                limit_count,
                before_timestamp,
                fetched_at,
                row_count
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, pool_address, timeframe, aggregate, source, limit_count, before_timestamp) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                row_count = excluded.row_count
            """,
            (
                network,
                pool_address,
                timeframe,
                aggregate,
                source,
                limit,
                before_timestamp if before_timestamp is not None else -1,
                isoformat_utc(fetched_at or utcnow()),
                row_count,
            ),
        )
        self._conn.commit()

    def get_external_json_cache(self, cache_key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT cache_key, fetched_at, value_json
            FROM external_json_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        return {
            "cache_key": row["cache_key"],
            "fetched_at": row["fetched_at"],
            "value": json_loads(row["value_json"], {}),
        }

    def upsert_external_json_cache(
        self,
        cache_key: str,
        value: dict[str, Any],
        *,
        fetched_at: datetime | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO external_json_cache(cache_key, fetched_at, value_json)
            VALUES(?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                value_json = excluded.value_json
            """,
            (cache_key, isoformat_utc(fetched_at or utcnow()), json_dumps(value)),
        )
        self._conn.commit()

    def list_snapshot_context(self, pair_address: str, since: datetime) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT observed_at, price_usd, volume_h1, market_cap, fdv
            FROM snapshots
            WHERE pair_address = ? AND observed_at >= ?
            ORDER BY observed_at ASC
            """,
            (pair_address, isoformat_utc(since)),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_active_pairs(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM pairs WHERE active = 1").fetchone()
        return int(row["n"])

    def count_pairs_by_state(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) AS n FROM pairs GROUP BY state",
        ).fetchall()
        return {row["state"]: int(row["n"]) for row in rows}

    def count_recent_alerts(self, since: datetime) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM alerts WHERE sent_at >= ?",
            (isoformat_utc(since),),
        ).fetchone()
        return int(row["n"])

    def get_dashboard_status(self, since: datetime) -> dict[str, Any]:
        latest_snapshot_row = self._conn.execute(
            """
            SELECT observed_at
            FROM snapshots
            ORDER BY observed_at DESC
            LIMIT 1
            """
        ).fetchone()
        recent_writes_row = self._conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM snapshots
            WHERE observed_at >= ?
            """,
            (isoformat_utc(since),),
        ).fetchone()
        recent_signal_row = self._conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM signals
            WHERE observed_at >= ?
            """,
            (isoformat_utc(since),),
        ).fetchone()
        return {
            "latest_snapshot_at": latest_snapshot_row["observed_at"] if latest_snapshot_row else None,
            "recent_snapshot_writes": int(recent_writes_row["n"]) if recent_writes_row else 0,
            "recent_signal_writes": int(recent_signal_row["n"]) if recent_signal_row else 0,
        }
