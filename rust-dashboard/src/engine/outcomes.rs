//! Outcome tracker — periodically resolve open simulated trades using snapshot data.

use super::trades;
use rusqlite::Connection;
use std::time::Duration;

/// Run outcome tracking loop: check every 5 minutes.
pub async fn run_outcome_tracker(db_path: &str) {
    println!("outcome tracker started (5 min interval)");
    loop {
        if let Err(e) = track_outcomes(db_path) {
            eprintln!("outcome tracking failed: {}", e);
        }
        tokio::time::sleep(Duration::from_secs(300)).await; // 5 minutes
    }
}

/// Find and resolve expired trades.
fn track_outcomes(db_path: &str) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let conn = Connection::open(db_path)?;
    conn.execute_batch("PRAGMA busy_timeout = 5000;")?;

    let expired = trades::find_expired_trades(&conn)?;

    for trade in expired {
        let trade_id = trade["id"].as_i64().unwrap_or(0);
        let pair_address = trade["pair_address"].as_str().unwrap_or_default();

        // Get max price during holding period from our own snapshots
        let (max_price, last_price): (Option<f64>, Option<f64>) = conn
            .query_row(
                "SELECT MAX(price_usd),
                        (SELECT price_usd FROM snapshots
                         WHERE pair_address = ?2
                         ORDER BY observed_at DESC LIMIT 1)
                 FROM snapshots
                 WHERE pair_address = ?2
                   AND observed_at >= ?1
                   AND price_usd IS NOT NULL",
                rusqlite::params![trade["entry_at"].as_str().unwrap_or(""), pair_address],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap_or((None, None));

        if let (Some(max_p), Some(last_p)) = (max_price, last_price) {
            if max_p > 0.0 && last_p > 0.0 {
                trades::resolve_trade(&conn, trade_id, last_p, max_p)?;
                println!(
                    "resolved trade #{}: entry={:.6} max={:.6} last={:.6}",
                    trade_id,
                    trade["entry_price_usd"].as_f64().unwrap_or(0.0),
                    max_p,
                    last_p
                );
            }
        }
    }

    Ok(())
}
