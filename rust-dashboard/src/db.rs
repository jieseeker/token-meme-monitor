//! Database layer — SQLite schema + CRUD operations.
//! Compatible with the existing Python-created database (same schema).

use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};

fn connect(path: &str) -> Result<Connection, rusqlite::Error> {
    let conn = Connection::open(path)?;
    conn.execute_batch(
        "PRAGMA journal_mode = WAL;
         PRAGMA synchronous = NORMAL;
         PRAGMA foreign_keys = ON;
         PRAGMA busy_timeout = 5000;",
    )?;
    Ok(conn)
}

pub fn scan_cursor(path: &str, key: &str) -> Result<Option<String>, rusqlite::Error> {
    connect(path)?
        .query_row(
            "SELECT cursor_value FROM scan_cursors WHERE cursor_key = ?1",
            [key],
            |row| row.get(0),
        )
        .optional()
}

pub fn token_snapshot_recent(
    path: &str,
    token: &str,
    max_age_seconds: i64,
) -> Result<bool, rusqlite::Error> {
    let cutoff = (chrono::Utc::now() - chrono::Duration::seconds(max_age_seconds)).to_rfc3339();
    connect(path)?.query_row(
        "SELECT EXISTS(SELECT 1 FROM pairs WHERE token_address = ?1 AND active = 1)
         AND NOT EXISTS(SELECT 1 FROM pairs WHERE token_address = ?1 AND active = 1
         AND (last_snapshot_at IS NULL OR datetime(last_snapshot_at) < datetime(?2)))",
        params![token, cutoff],
        |row| row.get(0),
    )
}

pub fn short_cycle_priority_tokens(path: &str) -> Result<Vec<String>, rusqlite::Error> {
    let conn = connect(path)?;
    let mut stmt = conn.prepare(
        "SELECT p.token_address FROM pairs p
         JOIN signals s ON s.id = (
             SELECT id FROM signals WHERE pair_address = p.pair_address
             ORDER BY observed_at DESC LIMIT 1
         )
         WHERE p.active = 1 AND s.score > 0
           AND datetime(s.observed_at) >= datetime('now', '-10 minutes')
           AND (s.reasons LIKE '%\"m5_buy_dominance\"%'
                OR s.reasons LIKE '%\"m5_volume_impulse\"%')
         GROUP BY p.token_address ORDER BY MIN(s.observed_at) LIMIT 20",
    )?;
    let rows = stmt.query_map([], |row| row.get(0))?.collect();
    rows
}

/// Combined liquidity for exactly the current pool set, using each pool's latest
/// snapshot in the 15–25 minute comparison window.
pub fn prior_pool_liquidity(
    path: &str,
    token: &str,
    pairs: &[String],
    now: chrono::DateTime<chrono::Utc>,
) -> Result<Option<f64>, rusqlite::Error> {
    if pairs.is_empty() {
        return Ok(None);
    }
    let conn = connect(path)?;
    let from = (now - chrono::Duration::minutes(25)).to_rfc3339();
    let until = (now - chrono::Duration::minutes(15)).to_rfc3339();
    let mut observed = std::collections::HashSet::new();
    let mut stmt = conn.prepare("SELECT DISTINCT pair_address FROM snapshots WHERE token_address = ?1 AND datetime(observed_at) BETWEEN datetime(?2) AND datetime(?3)")?;
    let found = stmt.query_map(params![token, from, until], |row| row.get::<_, String>(0))?;
    for pair in found {
        observed.insert(pair?);
    }
    if observed.len() != pairs.len() || !pairs.iter().all(|pair| observed.contains(pair)) {
        return Ok(None);
    }
    let mut total = 0.0;
    for pair in pairs {
        let value: Option<Option<f64>> = conn.query_row(
            "SELECT liquidity_usd FROM snapshots WHERE pair_address = ?1 AND datetime(observed_at) BETWEEN datetime(?2) AND datetime(?3) ORDER BY observed_at DESC LIMIT 1",
            params![pair, from, until], |row| row.get(0),
        ).optional()?;
        let Some(liquidity) = value
            .flatten()
            .filter(|value| value.is_finite() && *value >= 0.0)
        else {
            return Ok(None);
        };
        total += liquidity;
    }
    Ok(Some(total))
}

pub fn set_scan_cursor(path: &str, key: &str, value: &str) -> Result<(), rusqlite::Error> {
    connect(path)?.execute(
        "INSERT INTO scan_cursors(cursor_key, cursor_value, updated_at) VALUES (?1, ?2, ?3)
         ON CONFLICT(cursor_key) DO UPDATE SET cursor_value = excluded.cursor_value, updated_at = excluded.updated_at",
        params![key, value, chrono::Utc::now().to_rfc3339()],
    )?;
    Ok(())
}

pub fn init_schema(path: &str) -> Result<(), rusqlite::Error> {
    let conn = connect(path)?;
    conn.execute_batch(
        "
        CREATE TABLE IF NOT EXISTS scan_cursors (
            cursor_key TEXT PRIMARY KEY,
            cursor_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS detail_cache (
            kind TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (kind, cache_key)
        );
        CREATE TABLE IF NOT EXISTS tokens (
            token_address TEXT PRIMARY KEY,
            symbol TEXT,
            name TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
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
            first_seen_block INTEGER NOT NULL DEFAULT 0,
            first_seen_tx_hash TEXT NOT NULL DEFAULT '',
            first_seen_log_index INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'new',
            active INTEGER NOT NULL DEFAULT 1,
            risk_flags TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            last_snapshot_at TEXT,
            next_refresh_at TEXT,
            updated_at TEXT NOT NULL
        );
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
            volume_m5 REAL NOT NULL DEFAULT 0,
            volume_h1 REAL NOT NULL DEFAULT 0,
            volume_h24 REAL NOT NULL DEFAULT 0,
            buys_m5 INTEGER NOT NULL DEFAULT 0,
            sells_m5 INTEGER NOT NULL DEFAULT 0,
            buys_h1 INTEGER NOT NULL DEFAULT 0,
            sells_h1 INTEGER NOT NULL DEFAULT 0,
            price_change_m5 REAL NOT NULL DEFAULT 0,
            price_change_h1 REAL NOT NULL DEFAULT 0,
            price_change_h24 REAL NOT NULL DEFAULT 0,
            website_count INTEGER NOT NULL DEFAULT 0,
            social_count INTEGER NOT NULL DEFAULT 0,
            boosts_active INTEGER NOT NULL DEFAULT 0,
            age_minutes REAL NOT NULL DEFAULT 0,
            risk_flags TEXT NOT NULL DEFAULT '[]',
            raw_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(pair_address, observed_at)
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_pair ON snapshots(pair_address, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_snapshots_token ON snapshots(token_address, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_snapshots_observed_at ON snapshots(observed_at);
        CREATE TABLE IF NOT EXISTS signal_v2_state (
            token_address TEXT PRIMARY KEY,
            pair_address TEXT NOT NULL,
            first_seen_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            last_consumed_at TEXT NOT NULL,
            state_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            settings_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_signal_v2_state_first_seen ON signal_v2_state(first_seen_at);
        CREATE INDEX IF NOT EXISTS idx_signal_v2_state_updated ON signal_v2_state(updated_at DESC);
        CREATE TABLE IF NOT EXISTS signal_v2_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_address TEXT NOT NULL,
            pair_address TEXT NOT NULL,
            recorded_at INTEGER NOT NULL,
            summary_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_signal_v2_changes_token_time ON signal_v2_changes(token_address,recorded_at DESC);
        CREATE INDEX IF NOT EXISTS idx_signal_v2_changes_time ON signal_v2_changes(recorded_at);
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair_address TEXT NOT NULL,
            token_address TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            score INTEGER NOT NULL,
            pair_state TEXT NOT NULL,
            should_alert INTEGER NOT NULL DEFAULT 0,
            reasons TEXT NOT NULL DEFAULT '[]',
            risk_flags TEXT NOT NULL DEFAULT '[]',
            feature_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(pair_address, observed_at, strategy_version)
        );
        CREATE INDEX IF NOT EXISTS idx_signals_pair ON signals(pair_address, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_signals_observed_at ON signals(observed_at);

        CREATE TABLE IF NOT EXISTS signal_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            predictor_version TEXT NOT NULL,
            prob_2h_up20 REAL,
            prob_6h_up50 REAL,
            prob_24h_up100 REAL,
            risk_6h_dd30 REAL,
            opportunity_score INTEGER,
            short_momentum_score INTEGER,
            continuation_score INTEGER,
            breakout_score INTEGER,
            stage TEXT,
            reasons_json TEXT NOT NULL DEFAULT '[]',
            UNIQUE(signal_id, predictor_version),
            FOREIGN KEY (signal_id) REFERENCES signals(id)
        );
    ",
    )?;
    Ok(())
}

pub fn prune_history(path: &str) -> Result<bool, rusqlite::Error> {
    let cutoff = (chrono::Utc::now() - chrono::Duration::days(7)).to_rfc3339();
    let mut conn = connect(path)?;
    let tx = conn.transaction()?;
    let signal_cutoff = chrono::Utc::now().timestamp() - 7 * 86_400;
    tx.execute("DELETE FROM signal_v2_state WHERE token_address IN (SELECT token_address FROM signal_v2_state WHERE first_seen_at <= ?1 LIMIT 10000)", [signal_cutoff])?;
    tx.execute("DELETE FROM signal_v2_changes WHERE id IN (SELECT id FROM signal_v2_changes WHERE recorded_at <= ?1 LIMIT 10000)", [signal_cutoff])?;
    tx.execute("DELETE FROM detail_cache WHERE updated_at < ?1", [&cutoff])?;
    tx.execute("CREATE TEMP TABLE old_signals AS SELECT id FROM signals WHERE observed_at < ?1 LIMIT 10000", [&cutoff])?;
    tx.execute("CREATE TEMP TABLE old_snapshots AS SELECT id FROM snapshots WHERE observed_at < ?1 LIMIT 10000", [&cutoff])?;
    for table in [
        "signal_predictions",
        "simulated_trades",
        "alerts",
        "outcomes",
        "signal_feature_archives",
        "signal_prediction_outcomes",
    ] {
        let exists: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1)",
            [table],
            |row| row.get(0),
        )?;
        if exists {
            tx.execute(
                &format!("DELETE FROM {table} WHERE signal_id IN (SELECT id FROM old_signals)"),
                [],
            )?;
        }
    }
    let archives: bool = tx.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshot_raw_archives')",
        [], |row| row.get(0))?;
    if archives {
        tx.execute(
            "DELETE FROM snapshot_raw_archives WHERE snapshot_id IN (SELECT id FROM old_snapshots)",
            [],
        )?;
    }
    let signals = tx.execute(
        "DELETE FROM signals WHERE id IN (SELECT id FROM old_signals)",
        [],
    )?;
    let snapshots = tx.execute(
        "DELETE FROM snapshots WHERE id IN (SELECT id FROM old_snapshots)",
        [],
    )?;
    let more: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM signals WHERE observed_at < ?1)
        OR EXISTS(SELECT 1 FROM snapshots WHERE observed_at < ?1)
        OR EXISTS(SELECT 1 FROM signal_v2_state WHERE first_seen_at <= ?2)
        OR EXISTS(SELECT 1 FROM signal_v2_changes WHERE recorded_at <= ?2)",
        params![&cutoff, signal_cutoff],
        |row| row.get(0),
    )?;
    tx.commit()?;
    if signals + snapshots > 0 {
        crate::logging::event(
            "info",
            "history_cleanup_completed",
            serde_json::json!({"signals_removed":signals,"snapshots_removed":snapshots,"more":more}),
        );
    }
    if !more {
        let pages: i64 = conn.pragma_query_value(None, "page_count", |row| row.get(0))?;
        let free: i64 = conn.pragma_query_value(None, "freelist_count", |row| row.get(0))?;
        if free > 25_000 && free * 4 > pages {
            conn.execute_batch("VACUUM")?;
            crate::logging::event(
                "info",
                "history_database_compacted",
                serde_json::json!({"free_pages":free,"total_pages":pages}),
            );
        }
    }
    Ok(more)
}

pub fn save_detail_cache(
    path: &str,
    kind: &str,
    key: &str,
    value: &Value,
) -> Result<(), rusqlite::Error> {
    connect(path)?.execute(
        "INSERT INTO detail_cache(kind, cache_key, value_json, updated_at) VALUES (?1, ?2, ?3, ?4)
         ON CONFLICT(kind, cache_key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
        params![kind, key, value.to_string(), chrono::Utc::now().to_rfc3339()],
    )?;
    Ok(())
}

pub fn load_detail_cache(
    path: &str,
) -> Result<Vec<(String, String, Value, String)>, rusqlite::Error> {
    let conn = connect(path)?;
    let mut stmt = conn.prepare(
        "SELECT kind, cache_key, value_json, updated_at FROM detail_cache WHERE updated_at >= ?1",
    )?;
    let since = (chrono::Utc::now() - chrono::Duration::minutes(15)).to_rfc3339();
    let rows = stmt.query_map([since], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(3)?,
        ))
    })?;
    Ok(rows
        .filter_map(|row| {
            let (kind, key, raw, at) = row.ok()?;
            Some((kind, key, serde_json::from_str(&raw).ok()?, at))
        })
        .collect())
}

// ============ Write operations ============

use crate::engine::features::PairSnapshot;
use crate::engine::signals::SignalDecision;

/// Upsert a pair from Binance Alpha + write snapshot and signal.
pub fn upsert_pair_and_write(
    path: &str,
    token: &crate::clients::binance_alpha::AlphaToken,
    pair_address: &str,
    dex_id: &str,
    snapshot: &PairSnapshot,
    decision: &SignalDecision,
    raw_json: &Value,
) -> Result<(), rusqlite::Error> {
    let mut conn = connect(path)?;
    let tx = conn.transaction()?;
    let now = chrono::Utc::now().to_rfc3339();

    // Upsert token
    tx.execute(
        "INSERT INTO tokens (token_address, symbol, name, first_seen_at, last_seen_at, metadata_json)
         VALUES (?,?,?,?,?,?)
         ON CONFLICT(token_address) DO UPDATE SET symbol=?, name=?, last_seen_at=?, metadata_json=?",
        params![
            token.address, token.symbol, token.name, now, now,
            serde_json::to_string(&token.raw).unwrap_or_default(),
            token.symbol, token.name, now,
            serde_json::to_string(&token.raw).unwrap_or_default(),
        ],
    )?;

    // Upsert pair
    tx.execute(
        "INSERT INTO pairs
         (pair_address, chain_id, dex_id, token_address, token_symbol, token_name,
          quote_token_address, quote_symbol, token0_address, token1_address,
          pair_created_at, discovered_at, first_seen_block, first_seen_tx_hash,
          first_seen_log_index, state, active, last_snapshot_at, next_refresh_at, updated_at)
         VALUES (?,'bsc',?,?,?,?,'','','','',?,?,0,'',0,'active',1,?,?,?)
         ON CONFLICT(pair_address) DO UPDATE SET
           dex_id=?, last_snapshot_at=?, next_refresh_at=?, updated_at=?, active=1",
        params![
            // INSERT: pair_address, dex_id, token_address, token_symbol, token_name,
            //         pair_created_at, discovered_at,
            //         last_snapshot_at, next_refresh_at, updated_at
            pair_address,
            dex_id,
            token.address,
            token.symbol,
            token.name,
            snapshot.pair_created_at,
            now,
            now,
            now,
            now,
            // UPDATE: dex_id, last_snapshot_at, next_refresh_at, updated_at
            dex_id,
            now,
            now,
            now,
        ],
    )?;
    // Insert snapshot
    tx.execute(
        "INSERT OR IGNORE INTO snapshots
         (pair_address, token_address, observed_at, price_usd, price_native,
          liquidity_usd, fdv, market_cap, volume_m5, volume_h1, volume_h24,
          buys_m5, sells_m5, buys_h1, sells_h1,
          price_change_m5, price_change_h1, price_change_h24,
          website_count, social_count, boosts_active, age_minutes, risk_flags, raw_json)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        params![
            snapshot.pair_address,
            snapshot.token_address,
            snapshot.observed_at,
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
            0.0,
            serde_json::to_string(&decision.risk_flags).unwrap_or_default(),
            raw_json.to_string(),
        ],
    )?;

    // Insert signal
    tx.execute(
        "INSERT OR IGNORE INTO signals
         (pair_address, token_address, observed_at, strategy_version,
          score, pair_state, should_alert, reasons, risk_flags, feature_json)
         VALUES (?,?,?,?,?,?,?,?,?,?)",
        params![
            snapshot.pair_address,
            snapshot.token_address,
            decision.observed_at,
            decision.strategy_version,
            decision.score,
            decision.pair_state,
            decision.should_alert as i32,
            serde_json::to_string(&decision.reasons).unwrap_or_default(),
            serde_json::to_string(&decision.risk_flags).unwrap_or_default(),
            "{}",
        ],
    )?;

    tx.commit()?;
    Ok(())
}

pub fn retire_missing_pools(
    path: &str,
    token_address: &str,
    current: &[String],
) -> Result<(), rusqlite::Error> {
    let mut conn = connect(path)?;
    let tx = conn.transaction()?;
    let old: Vec<String> = {
        let mut stmt = tx.prepare("SELECT pair_address FROM pairs WHERE chain_id='bsc' AND token_address=?1 COLLATE NOCASE AND dex_id IN ('pancakeswap','uniswap') AND active=1")?;
        let rows = stmt
            .query_map([token_address], |row| row.get(0))?
            .collect::<Result<_, _>>()?;
        rows
    };
    for pair in old {
        if !current.iter().any(|seen| seen.eq_ignore_ascii_case(&pair)) {
            tx.execute("UPDATE pairs SET active=0 WHERE pair_address=?1", [pair])?;
        }
    }
    tx.commit()
}

pub fn retire_missing_alpha_tokens(
    path: &str,
    current: &[String],
) -> Result<usize, rusqlite::Error> {
    let active: std::collections::HashSet<_> = current
        .iter()
        .map(|address| address.to_ascii_lowercase())
        .collect();
    let mut conn = connect(path)?;
    let tx = conn.transaction()?;
    let missing: Vec<String> = {
        let mut stmt = tx.prepare(
            "SELECT pair_address, token_address FROM pairs WHERE chain_id='bsc' AND active=1",
        )?;
        let rows: Vec<(String, String)> = stmt
            .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?
            .collect::<Result<_, _>>()?;
        rows.into_iter()
            .filter(|(_, token)| !active.contains(&token.to_ascii_lowercase()))
            .map(|(pair, _)| pair)
            .collect()
    };
    for pair in &missing {
        tx.execute("UPDATE pairs SET active=0 WHERE pair_address=?1", [pair])?;
    }
    tx.commit()?;
    Ok(missing.len())
}

// ============ Read operations (for worker + dashboard) ============

fn row_to_json(row: &rusqlite::Row, cols: &[String]) -> Result<Value, rusqlite::Error> {
    let mut obj = serde_json::Map::new();
    for (i, col) in cols.iter().enumerate() {
        let val = match row.get_ref(i)? {
            rusqlite::types::ValueRef::Null => Value::Null,
            rusqlite::types::ValueRef::Integer(n) => json!(n),
            rusqlite::types::ValueRef::Real(f) => json!(f),
            rusqlite::types::ValueRef::Text(t) => json!(String::from_utf8_lossy(t)),
            rusqlite::types::ValueRef::Blob(b) => json!(String::from_utf8_lossy(b).to_string()),
        };
        obj.insert(col.clone(), val);
    }
    Ok(Value::Object(obj))
}

fn query_to_json(
    conn: &Connection,
    sql: &str,
    params: &[&dyn rusqlite::ToSql],
) -> Result<Vec<Value>, rusqlite::Error> {
    let mut stmt = conn.prepare(sql)?;
    let col_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
    let mut rows = stmt.query(params)?;
    let mut results = Vec::new();
    while let Some(row) = rows.next()? {
        results.push(row_to_json(row, &col_names)?);
    }
    Ok(results)
}

// ============ Dashboard queries ============

pub fn futures_symbol(path: &str, pair: &str) -> Result<Option<String>, rusqlite::Error> {
    let conn = connect(path)?;
    let row: Option<(String, i64)> = conn.query_row(
        "SELECT p.token_symbol, (SELECT COUNT(DISTINCT token_address) FROM pairs WHERE UPPER(token_symbol) = UPPER(p.token_symbol) AND chain_id='bsc' AND active=1)
         FROM pairs p WHERE p.pair_address = ? AND p.chain_id='bsc' AND p.active=1",
        [pair], |r| Ok((r.get(0)?, r.get(1)?)),
    ).optional()?;
    Ok(row.and_then(|(base, count)| {
        let base = base.to_ascii_uppercase();
        (count == 1
            && !base.is_empty()
            && base.len() <= 15
            && base.bytes().all(|b| b.is_ascii_alphanumeric()))
        .then(|| format!("{base}USDT"))
    }))
}

pub fn market_price_identity(
    path: &str,
    pair: &str,
) -> Result<Option<(String, bool, f64)>, rusqlite::Error> {
    let Some(symbol) = futures_symbol(path, pair)? else {
        return Ok(None);
    };
    let conn = connect(path)?;
    let row: Option<(String, Option<f64>)> = conn.query_row(
        "SELECT t.metadata_json, (SELECT price_usd FROM snapshots WHERE pair_address = p.pair_address ORDER BY observed_at DESC LIMIT 1)
         FROM pairs p JOIN tokens t ON t.token_address = p.token_address WHERE p.pair_address = ?",
        [pair], |r| Ok((r.get(0)?, r.get(1)?)),
    ).optional()?;
    Ok(row.and_then(|(metadata, price)| {
        let price = price.filter(|value| value.is_finite() && *value > 0.0)?;
        let meta: Value = serde_json::from_str(&metadata).ok()?;
        let base = symbol.strip_suffix("USDT")?;
        let spot = meta["listingCex"] == true
            && meta["cexCoinName"]
                .as_str()
                .is_some_and(|name| name.eq_ignore_ascii_case(base));
        Some((symbol, spot, price))
    }))
}

pub fn overview(path: &str, limit: usize) -> Result<Vec<Value>, rusqlite::Error> {
    let conn = connect(path)?;
    let sql = r#"
        SELECT
            p.pair_address,
            p.token_address,
            COALESCE(p.token_symbol, substr(p.token_address, 1, 8)) AS token_symbol,
            COALESCE(p.token_name, '') AS token_name,
            p.quote_symbol, p.dex_id, p.state, p.active,
            snap.observed_at, snap.price_usd, snap.liquidity_usd,
            snap.market_cap, snap.fdv, snap.volume_h1, snap.volume_m5, snap.volume_h24,
            snap.price_change_m5, snap.price_change_h1, snap.price_change_h24,
            snap.buys_h1, snap.sells_h1,
            s.observed_at AS signal_at, s.score AS signal_score,
            s.pair_state AS signal_pair_state, s.should_alert AS signal_alert,
            s.reasons AS signal_reasons, s.risk_flags AS signal_risk_flags,
            pred.prob_2h_up20, pred.prob_6h_up50, pred.prob_24h_up100,
            pred.risk_6h_dd30, pred.opportunity_score,
            COALESCE(pred.short_momentum_score, pred.opportunity_score) AS short_momentum_score,
            pred.continuation_score, pred.breakout_score,
            pred.stage AS prediction_stage, pred.reasons_json AS prediction_reasons
        FROM pairs p
        LEFT JOIN snapshots snap ON snap.id = (
            SELECT snap2.id FROM snapshots snap2
            WHERE snap2.pair_address = p.pair_address
            ORDER BY snap2.observed_at DESC LIMIT 1
        )
        LEFT JOIN signals s ON s.id = (
            SELECT s2.id FROM signals s2
            WHERE s2.pair_address = p.pair_address
            ORDER BY s2.observed_at DESC LIMIT 1
        )
        LEFT JOIN signal_predictions pred ON pred.signal_id = s.id
        WHERE p.active = 1
            AND snap.observed_at IS NOT NULL
            AND datetime(snap.observed_at) > datetime('now', '-10 minutes')
        ORDER BY
            COALESCE(pred.short_momentum_score, pred.opportunity_score, 0) DESC,
            COALESCE(s.score, 0) DESC,
            snap.observed_at DESC
        LIMIT ?
    "#;
    query_to_json(&conn, sql, &[&limit])
}

pub fn pair_for_depth(path: &str, pair: &str) -> Result<Option<Value>, rusqlite::Error> {
    let conn = connect(path)?;
    Ok(query_to_json(
        &conn,
        "SELECT p.pair_address, p.token_address, p.dex_id, p.chain_id,
                s.price_usd, s.liquidity_usd
         FROM pairs p JOIN snapshots s ON s.id = (
            SELECT id FROM snapshots WHERE pair_address = p.pair_address
            ORDER BY observed_at DESC LIMIT 1)
         WHERE p.pair_address = ? AND p.active = 1",
        &[&pair],
    )?
    .into_iter()
    .next())
}

pub fn snapshots(path: &str, pair: &str, limit: usize) -> Result<Vec<Value>, rusqlite::Error> {
    let conn = connect(path)?;
    query_to_json(
        &conn,
        "SELECT observed_at, price_usd, price_native, liquidity_usd, market_cap, fdv,
                volume_m5, volume_h1, volume_h24,
                price_change_m5, price_change_h1, price_change_h24,
                buys_m5, sells_m5, buys_h1, sells_h1
         FROM snapshots WHERE pair_address = ?
         ORDER BY observed_at DESC LIMIT ?",
        &[&pair, &limit],
    )
}

pub fn signals(path: &str, pair: &str, limit: usize) -> Result<Vec<Value>, rusqlite::Error> {
    let conn = connect(path)?;
    query_to_json(
        &conn,
        "SELECT s.observed_at, s.score, s.pair_state, s.should_alert,
                s.reasons, s.risk_flags,
                pred.opportunity_score, pred.short_momentum_score,
                pred.continuation_score, pred.breakout_score, pred.stage
         FROM signals s
         LEFT JOIN signal_predictions pred ON pred.signal_id = s.id
         WHERE s.pair_address = ?
         ORDER BY s.observed_at DESC LIMIT ?",
        &[&pair, &limit],
    )
}

pub fn status(path: &str) -> Result<Value, rusqlite::Error> {
    let conn = connect(path)?;
    let count = |table: &str| -> i64 {
        conn.query_row(&format!("SELECT COUNT(*) FROM {}", table), [], |r| r.get(0))
            .unwrap_or(0)
    };
    let latest: Option<String> = conn
        .query_row("SELECT MAX(observed_at) FROM snapshots", [], |r| r.get(0))
        .unwrap_or(None);
    Ok(json!({
        "tables": {
            "pairs": count("pairs"),
            "snapshots": count("snapshots"),
            "signals": count("signals"),
            "signal_predictions": count("signal_predictions"),
        },
        "latest_snapshot_at": latest,
    }))
}

#[cfg(test)]
mod pool_tests {
    #[test]
    fn structure_snapshot_lookup_uses_token_index() {
        let path = std::env::temp_dir().join(format!(
            "tmm-structure-index-{}-{}.db",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let path = path.to_str().unwrap();
        super::init_schema(path).unwrap();
        let conn = rusqlite::Connection::open(path).unwrap();
        let plan: String = conn
            .query_row(
                "EXPLAIN QUERY PLAN SELECT pair_address,observed_at FROM snapshots WHERE token_address=?1 ORDER BY observed_at DESC LIMIT 2000",
                ["0xtoken"],
                |row| row.get(3),
            )
            .unwrap();
        assert!(plan.contains("idx_snapshots_token"), "{plan}");
        drop(conn);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn futures_symbol_ignores_retired_token_with_same_symbol() {
        let path = std::env::temp_dir().join(format!(
            "tmm-futures-symbol-{}-{}.db",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let path = path.to_str().unwrap();
        super::init_schema(path).unwrap();
        let conn = rusqlite::Connection::open(path).unwrap();
        for (pair, token, active) in [("current", "current-token", 1), ("retired", "old-token", 0)]
        {
            conn.execute(
                "INSERT INTO pairs(pair_address,chain_id,token_address,token_symbol,quote_token_address,quote_symbol,token0_address,token1_address,pair_created_at,discovered_at,updated_at,active)
                 VALUES (?1,'bsc',?2,'TAC','q','Q',?2,'q',datetime('now'),datetime('now'),datetime('now'),?3)",
                rusqlite::params![pair, token, active],
            ).unwrap();
        }
        assert_eq!(
            super::futures_symbol(path, "current").unwrap().as_deref(),
            Some("TACUSDT")
        );
        assert_eq!(super::futures_symbol(path, "retired").unwrap(), None);
        drop(conn);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn token_is_recent_only_when_every_active_pool_is_recent() {
        let path =
            std::env::temp_dir().join(format!("tmm-pool-freshness-{}.db", std::process::id()));
        let path = path.to_str().unwrap();
        super::init_schema(path).unwrap();
        let conn = rusqlite::Connection::open(path).unwrap();
        for (pair, age) in [("fresh", 1), ("old", 7)] {
            conn.execute(
                "INSERT INTO pairs(pair_address,chain_id,token_address,quote_token_address,quote_symbol,token0_address,token1_address,pair_created_at,discovered_at,updated_at,last_snapshot_at)
                 VALUES (?1,'bsc','token','q','Q','token','q',datetime('now'),datetime('now'),datetime('now'),?2)",
                rusqlite::params![pair, (chrono::Utc::now() - chrono::Duration::minutes(age)).to_rfc3339()],
            ).unwrap();
        }
        assert!(!super::token_snapshot_recent(path, "missing", 300).unwrap());
        assert!(!super::token_snapshot_recent(path, "token", 300).unwrap());
        conn.execute("UPDATE pairs SET active = 0 WHERE pair_address = 'old'", [])
            .unwrap();
        assert!(super::token_snapshot_recent(path, "token", 300).unwrap());
        drop(conn);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn short_cycle_priority_only_uses_current_active_signal() {
        let path = std::env::temp_dir().join(format!(
            "tmm-priority-{}-{}.db",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let path = path.to_str().unwrap();
        super::init_schema(path).unwrap();
        let conn = rusqlite::Connection::open(path).unwrap();
        for (pair, token, active) in [("p1", "a", 1), ("p2", "b", 1), ("p3", "c", 0)] {
            conn.execute(
                "INSERT INTO pairs(pair_address,chain_id,token_address,quote_token_address,quote_symbol,token0_address,token1_address,pair_created_at,discovered_at,updated_at,active)
                 VALUES (?1,'bsc',?2,'q','Q',?2,'q',datetime('now'),datetime('now'),datetime('now'),?3)",
                rusqlite::params![pair, token, active],
            ).unwrap();
            conn.execute(
                "INSERT INTO signals(pair_address,token_address,observed_at,strategy_version,score,pair_state,reasons)
                 VALUES (?1,?2,datetime('now','-4 minutes'),'v1',40,'watching','[\"m5_buy_dominance\"]')",
                rusqlite::params![pair, token],
            ).unwrap();
        }
        conn.execute(
            "INSERT INTO signals(pair_address,token_address,observed_at,strategy_version,score,pair_state,reasons)
             VALUES ('p2','b',datetime('now'),'v1',40,'watching','[\"h1_volume_support\"]')",
            [],
        ).unwrap();
        assert_eq!(super::short_cycle_priority_tokens(path).unwrap(), vec!["a"]);
        drop(conn);
        std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn prior_liquidity_requires_every_current_pool_in_window() {
        let path = std::env::temp_dir().join(format!(
            "tmm-prior-{}-{}.db",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let path = path.to_str().unwrap();
        super::init_schema(path).unwrap();
        let now = chrono::Utc::now();
        let conn = rusqlite::Connection::open(path).unwrap();
        for (pair, age, liquidity) in [("a", 20, 20_000.0), ("b", 19, 10_000.0), ("a", 8, 12_000.0)]
        {
            conn.execute("INSERT INTO snapshots(pair_address,token_address,observed_at,liquidity_usd) VALUES (?1,'t',?2,?3)", rusqlite::params![pair, (now-chrono::Duration::minutes(age)).to_rfc3339(), liquidity]).unwrap();
        }
        assert_eq!(
            super::prior_pool_liquidity(path, "t", &["a".into(), "b".into()], now).unwrap(),
            Some(30_000.0)
        );
        assert_eq!(
            super::prior_pool_liquidity(path, "t", &["a".into(), "c".into()], now).unwrap(),
            None
        );
        conn.execute("INSERT INTO snapshots(pair_address,token_address,observed_at,liquidity_usd) VALUES ('c','t',?1,5000)", [(now-chrono::Duration::minutes(18)).to_rfc3339()]).unwrap();
        assert_eq!(
            super::prior_pool_liquidity(path, "t", &["a".into(), "b".into()], now).unwrap(),
            None
        );
        drop(conn);
        std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn detail_cache_restores_recent_success_only() {
        let path = std::env::temp_dir().join(format!(
            "tmm-detail-cache-{}-{}.db",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let path = path.to_str().unwrap();
        super::init_schema(path).unwrap();
        super::save_detail_cache(
            path,
            "depth",
            "pair",
            &serde_json::json!({"available":true,"support_usd":[100]}),
        )
        .unwrap();
        let rows = super::load_detail_cache(path).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].2["support_usd"][0], 100);
        let conn = rusqlite::Connection::open(path).unwrap();
        conn.execute(
            "UPDATE detail_cache SET updated_at = ?1",
            [(chrono::Utc::now() - chrono::Duration::minutes(16)).to_rfc3339()],
        )
        .unwrap();
        assert!(super::load_detail_cache(path).unwrap().is_empty());
    }

    #[test]
    fn cleanup_keeps_one_week_and_removes_signal_children() {
        let path = std::env::temp_dir().join(format!(
            "tmm-cleanup-{}-{}.db",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let path = path.to_str().unwrap();
        super::init_schema(path).unwrap();
        let conn = rusqlite::Connection::open(path).unwrap();
        let old = (chrono::Utc::now() - chrono::Duration::days(8)).to_rfc3339();
        let recent = (chrono::Utc::now() - chrono::Duration::days(6)).to_rfc3339();
        for (id, at) in [(1, &old), (2, &recent)] {
            conn.execute("INSERT INTO snapshots(id,pair_address,token_address,observed_at) VALUES (?1,'pair','token',?2)",
                rusqlite::params![id, at]).unwrap();
            conn.execute("INSERT INTO signals(id,pair_address,token_address,observed_at,strategy_version,score,pair_state)
                VALUES (?1,'pair','token',?2,'v1',0,'watching')", rusqlite::params![id, at]).unwrap();
            conn.execute(
                "INSERT INTO signal_predictions(signal_id,predictor_version) VALUES (?1,'v1')",
                [id],
            )
            .unwrap();
        }
        conn.execute_batch(
            "CREATE TABLE simulated_trades(signal_id INTEGER);
            INSERT INTO simulated_trades VALUES (1);
            CREATE TABLE signal_prediction_outcomes(signal_id INTEGER PRIMARY KEY);
            INSERT INTO signal_prediction_outcomes VALUES (1);",
        )
        .unwrap();
        drop(conn);
        assert!(!super::prune_history(path).unwrap());
        let conn = rusqlite::Connection::open(path).unwrap();
        for table in ["snapshots", "signals"] {
            let id: i64 = conn
                .query_row(&format!("SELECT id FROM {table}"), [], |row| row.get(0))
                .unwrap();
            assert_eq!(id, 2);
        }
        let signal_id: i64 = conn
            .query_row("SELECT signal_id FROM signal_predictions", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(signal_id, 2);
        for table in [
            "snapshots",
            "signals",
            "signal_predictions",
            "simulated_trades",
            "signal_prediction_outcomes",
        ] {
            let count: i64 = conn
                .query_row(&format!("SELECT count(*) FROM {table}"), [], |row| {
                    row.get(0)
                })
                .unwrap();
            assert_eq!(
                count,
                if matches!(table, "snapshots" | "signals" | "signal_predictions") {
                    1
                } else {
                    0
                }
            );
        }
        let violations: i64 = conn
            .query_row("SELECT count(*) FROM pragma_foreign_key_check", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(violations, 0);
        drop(conn);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn schema_keeps_multiple_pools_per_dex_and_retires_missing_pools() {
        let path = std::env::temp_dir().join(format!(
            "tmm-pools-{}-{}.db",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let path = path.to_str().unwrap();
        super::init_schema(path).unwrap();
        let conn = rusqlite::Connection::open(path).unwrap();
        for (pair, dex, at) in [
            ("old", "pancakeswap", "2026-10-22T00:00:00+00:00"),
            ("new", "pancakeswap", "2026-10-22T01:00:00+00:00"),
            ("uni", "uniswap", "2026-10-22T00:30:00+00:00"),
        ] {
            conn.execute("INSERT INTO pairs
                (pair_address,chain_id,dex_id,token_address,quote_token_address,quote_symbol,
                 token0_address,token1_address,pair_created_at,discovered_at,updated_at,last_snapshot_at)
                VALUES (?,'bsc',?,'0xtoken','','','','','','','',?)",
                rusqlite::params![pair,dex,at]).unwrap();
        }
        drop(conn);
        super::init_schema(path).unwrap();
        let conn = rusqlite::Connection::open(path).unwrap();
        let active = |pair: &str| -> i64 {
            conn.query_row(
                "SELECT active FROM pairs WHERE pair_address=?",
                [pair],
                |row| row.get(0),
            )
            .unwrap()
        };
        assert_eq!((active("old"), active("new"), active("uni")), (1, 1, 1));
        drop(conn);
        super::retire_missing_pools(path, "0xtoken", &["new".into(), "uni".into()]).unwrap();
        let conn = rusqlite::Connection::open(path).unwrap();
        let active = |pair: &str| -> i64 {
            conn.query_row(
                "SELECT active FROM pairs WHERE pair_address=?",
                [pair],
                |row| row.get(0),
            )
            .unwrap()
        };
        assert_eq!((active("old"), active("new"), active("uni")), (0, 1, 1));
        drop(conn);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn overview_excludes_snapshots_older_than_ten_minutes() {
        let path = std::env::temp_dir().join(format!(
            "tmm-fresh-{}-{}.db",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let path = path.to_str().unwrap();
        super::init_schema(path).unwrap();
        let conn = rusqlite::Connection::open(path).unwrap();
        conn.execute("INSERT INTO pairs(pair_address,chain_id,token_address,quote_token_address,quote_symbol,token0_address,token1_address,pair_created_at,discovered_at,updated_at) VALUES('pair','bsc','token','','','','','','','')", []).unwrap();
        let old = (chrono::Utc::now() - chrono::Duration::minutes(11)).to_rfc3339();
        conn.execute("INSERT INTO snapshots(pair_address,token_address,observed_at) VALUES('pair','token',?1)", [&old]).unwrap();
        drop(conn);
        assert!(super::overview(path, 10).unwrap().is_empty());
        let conn = rusqlite::Connection::open(path).unwrap();
        let fresh = chrono::Utc::now().to_rfc3339();
        conn.execute("INSERT INTO snapshots(pair_address,token_address,observed_at) VALUES('pair','token',?1)", [&fresh]).unwrap();
        conn.execute("INSERT INTO signals(pair_address,token_address,observed_at,strategy_version,score,pair_state,risk_flags) VALUES('pair','token',?1,'test',10,'active','[\"sell_pressure\"]')", [&fresh]).unwrap();
        drop(conn);
        let rows = super::overview(path, 10).unwrap();
        assert_eq!(rows[0]["observed_at"], fresh);
        assert_eq!(rows[0]["signal_risk_flags"], "[\"sell_pressure\"]");
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn alpha_sync_retires_only_missing_bsc_tokens() {
        let path = std::env::temp_dir().join(format!(
            "tmm-alpha-sync-{}-{}.db",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let path = path.to_str().unwrap();
        super::init_schema(path).unwrap();
        let conn = rusqlite::Connection::open(path).unwrap();
        for (pair, chain, token) in [
            ("live", "bsc", "0xlive"),
            ("owl-pancake", "bsc", "0xowl"),
            ("owl-uni", "bsc", "0xowl"),
            ("other-chain", "eth", "0xother"),
        ] {
            conn.execute("INSERT INTO pairs(pair_address,chain_id,token_address,quote_token_address,quote_symbol,token0_address,token1_address,pair_created_at,discovered_at,updated_at) VALUES(?1,?2,?3,'','','','','','','')", rusqlite::params![pair,chain,token]).unwrap();
        }
        drop(conn);
        let retired = super::retire_missing_alpha_tokens(path, &["0xLIVE".into()]).unwrap();
        assert_eq!(retired, 2);
        let conn = rusqlite::Connection::open(path).unwrap();
        for (pair, expected) in [
            ("live", 1),
            ("owl-pancake", 0),
            ("owl-uni", 0),
            ("other-chain", 1),
        ] {
            let active: i64 = conn
                .query_row(
                    "SELECT active FROM pairs WHERE pair_address=?1",
                    [pair],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(active, expected, "{pair}");
        }
        drop(conn);
        std::fs::remove_file(path).unwrap();
    }
}
