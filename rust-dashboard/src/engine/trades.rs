//! Simulated trade records — track "what if we bought at signal time".

use rusqlite::{params, Connection};
use serde_json::{json, Value};

/// Open a simulated trade when a signal fires above threshold.
pub fn open_simulated_trade(
    conn: &Connection,
    signal_id: i64,
    pair_address: &str,
    token_address: &str,
    token_symbol: &str,
    entry_price_usd: f64,
    signal_score: i32,
    short_momentum_score: Option<i32>,
) -> Result<i64, rusqlite::Error> {
    let now = chrono::Utc::now().to_rfc3339();

    // Don't duplicate: one trade per signal
    let existing: Option<i64> = conn
        .query_row(
            "SELECT id FROM simulated_trades WHERE signal_id = ?",
            params![signal_id],
            |r| r.get(0),
        )
        .map(Some)
        .or_else(|e| {
            if e == rusqlite::Error::QueryReturnedNoRows {
                Ok(None)
            } else {
                Err(e)
            }
        })?;

    if let Some(id) = existing {
        return Ok(id);
    }

    conn.execute(
        "INSERT INTO simulated_trades
         (signal_id, pair_address, token_address, token_symbol,
          entry_at, entry_price_usd,
          target_return_1, target_return_2, horizon_1_hours, horizon_2_hours,
          signal_score, short_momentum_score,
          status, created_at, updated_at)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'open', ?, ?)",
        params![
            signal_id,
            pair_address,
            token_address,
            token_symbol,
            now,
            entry_price_usd,
            0.20, // target 1: +20%
            0.50, // target 2: +50%
            2,    // horizon 1: 2 hours
            6,    // horizon 2: 6 hours
            signal_score,
            short_momentum_score,
            now,
            now,
        ],
    )?;

    Ok(conn.last_insert_rowid())
}

/// Resolve a trade using max price seen in snapshots during holding period.
pub fn resolve_trade(
    conn: &Connection,
    trade_id: i64,
    exit_price_usd: f64,
    max_price_usd: f64,
) -> Result<(), rusqlite::Error> {
    let trade = get_trade(conn, trade_id)?;
    let (entry_price, target1, target2, h1, h2, entry_at) = trade;

    if entry_price <= 0.0 {
        return Ok(());
    }

    let max_return = (max_price_usd - entry_price) / entry_price;
    let final_return = (exit_price_usd - entry_price) / entry_price;

    // Check if targets were hit
    let outcome_1 = if max_return >= target1 { "hit" } else { "miss" };
    let outcome_2 = if max_return >= target2 { "hit" } else { "miss" };

    // Determine status based on time elapsed
    let entry_time = chrono::DateTime::parse_from_rfc3339(&entry_at)
        .map(|d| d.with_timezone(&chrono::Utc))
        .unwrap_or_else(|_| chrono::Utc::now());
    let hours_elapsed = (chrono::Utc::now() - entry_time).num_hours();

    let status = if hours_elapsed >= h2 {
        "expired"
    } else if max_return >= target2 {
        "hit_2"
    } else if max_return >= target1 {
        "hit_1"
    } else if hours_elapsed >= h1 {
        "expired_1"
    } else {
        "open"
    };

    let now = chrono::Utc::now().to_rfc3339();
    conn.execute(
        "UPDATE simulated_trades
         SET status=?, exit_price_usd=?, max_price_usd=?, max_return_pct=?,
             outcome_1=?, outcome_2=?, exit_at=?, updated_at=?
         WHERE id=?",
        params![
            status,
            exit_price_usd,
            max_price_usd,
            max_return,
            outcome_1,
            outcome_2,
            now,
            now,
            trade_id,
        ],
    )?;

    Ok(())
}

type TradeInfo = (f64, f64, f64, i64, i64, String);

fn get_trade(conn: &Connection, trade_id: i64) -> Result<TradeInfo, rusqlite::Error> {
    conn.query_row(
        "SELECT entry_price_usd, target_return_1, target_return_2,
                horizon_1_hours, horizon_2_hours, entry_at
         FROM simulated_trades WHERE id = ?",
        params![trade_id],
        |r| {
            Ok((
                r.get(0)?,
                r.get(1)?,
                r.get(2)?,
                r.get(3)?,
                r.get(4)?,
                r.get(5)?,
            ))
        },
    )
}

/// Find open trades that need resolution.
pub fn find_expired_trades(conn: &Connection) -> Result<Vec<Value>, rusqlite::Error> {
    let mut stmt = conn.prepare(
        "SELECT id, pair_address, entry_at, entry_price_usd,
                horizon_1_hours, horizon_2_hours
         FROM simulated_trades
         WHERE status = 'open'
           AND entry_at < datetime('now', '-2 hours')
         ORDER BY entry_at ASC
         LIMIT 50",
    )?;

    let mut results = Vec::new();
    let mut rows = stmt.query([])?;
    while let Some(row) = rows.next()? {
        results.push(json!({
            "id": row.get::<_, i64>(0)?,
            "pair_address": row.get::<_, String>(1)?,
            "entry_at": row.get::<_, String>(2)?,
            "entry_price_usd": row.get::<_, f64>(3)?,
            "h1": row.get::<_, i64>(4)?,
            "h2": row.get::<_, i64>(5)?,
        }));
    }
    Ok(results)
}

/// Get performance summary grouped by signal score bands.
pub fn trade_summary(conn: &Connection) -> Result<Value, rusqlite::Error> {
    let mut stmt = conn.prepare(
        "SELECT
            CASE
                WHEN signal_score >= 70 THEN '70-100'
                WHEN signal_score >= 60 THEN '60-69'
                WHEN signal_score >= 50 THEN '50-59'
                WHEN signal_score >= 40 THEN '40-49'
                ELSE '<40'
            END as score_band,
            COUNT(*) as total,
            SUM(CASE WHEN outcome_1 = 'hit' THEN 1 ELSE 0 END) as hits_1,
            SUM(CASE WHEN outcome_2 = 'hit' THEN 1 ELSE 0 END) as hits_2,
            AVG(max_return_pct) as avg_max_return,
            AVG(CASE WHEN status != 'open' THEN (exit_price_usd - entry_price_usd) / entry_price_usd END) as avg_final_return
         FROM simulated_trades
         WHERE status != 'open'
         GROUP BY score_band
         ORDER BY score_band DESC",
    )?;

    let mut bands = Vec::new();
    let mut rows = stmt.query([])?;
    while let Some(row) = rows.next()? {
        bands.push(json!({
            "band": row.get::<_, String>(0)?,
            "total": row.get::<_, i64>(1)?,
            "target1_hits": row.get::<_, i64>(2)?,
            "target2_hits": row.get::<_, i64>(3)?,
            "target1_rate": row.get::<_, f64>(2).map(|h| h / row.get::<_, i64>(1).unwrap_or(1).max(1) as f64).unwrap_or(0.0),
            "avg_max_return": row.get::<_, Option<f64>>(4)?.unwrap_or(0.0),
            "avg_final_return": row.get::<_, Option<f64>>(5)?.unwrap_or(0.0),
        }));
    }

    // Overall stats
    let (total, open, resolved, t1_hits): (i64, i64, i64, i64) = conn.query_row(
        "SELECT COUNT(*),
                COALESCE(SUM(CASE WHEN status='open' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status!='open' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN outcome_1='hit' THEN 1 ELSE 0 END), 0)
         FROM simulated_trades",
        [],
        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
    )?;

    Ok(json!({
        "overall": {
            "total": total,
            "open": open,
            "resolved": resolved,
            "target1_hit_rate": if resolved > 0 { t1_hits as f64 / resolved as f64 } else { 0.0 },
        },
        "by_score_band": bands,
    }))
}
