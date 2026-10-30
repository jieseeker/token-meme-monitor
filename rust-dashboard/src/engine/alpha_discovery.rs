//! Full Binance Alpha discovery audit, retained before futures filtering.
use crate::clients::binance_alpha::AlphaToken;
use chrono::{DateTime, Utc};
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};
use std::collections::HashSet;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

fn number(value: &Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|text| text.parse().ok()))
        .filter(|value| value.is_finite())
}

fn bucket_start(at: DateTime<Utc>) -> DateTime<Utc> {
    let seconds = at.timestamp();
    DateTime::from_timestamp(seconds - seconds.rem_euclid(300), 0).unwrap_or(at)
}

pub fn record_snapshots(
    path: &str,
    tokens: &[AlphaToken],
    futures_addresses: &HashSet<String>,
    at: DateTime<Utc>,
) -> Result<usize> {
    let conn = Connection::open(path)?;
    conn.execute_batch("PRAGMA busy_timeout=5000")?;
    let bucket = bucket_start(at).to_rfc3339();
    let mut statement = conn.prepare(
        "INSERT INTO alpha_universe_snapshots
         (bucket_start,token_address,symbol,name,observed_at,price_usd,price_change_h24,
         volume_h24,liquidity_usd,market_cap,holders,futures_listed)
         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
         ON CONFLICT(token_address,bucket_start) DO UPDATE SET
           symbol=excluded.symbol,name=excluded.name,observed_at=excluded.observed_at,
           price_usd=excluded.price_usd,price_change_h24=excluded.price_change_h24,
           volume_h24=excluded.volume_h24,liquidity_usd=excluded.liquidity_usd,
           market_cap=excluded.market_cap,holders=excluded.holders,
           futures_listed=excluded.futures_listed",
    )?;
    let mut inserted = 0;
    for token in tokens {
        inserted += statement.execute(rusqlite::params![
            bucket,
            token.address,
            token.symbol,
            token.name,
            at.to_rfc3339(),
            number(&token.raw["price"]),
            number(&token.raw["percentChange24h"]),
            number(&token.raw["volume24h"]),
            number(&token.raw["liquidity"]),
            number(&token.raw["marketCap"]),
            token.raw["holders"].as_i64(),
            futures_addresses.contains(&token.address),
        ])?;
    }
    Ok(inserted)
}

pub fn audit(path: &str, top_n: usize) -> Result<Value> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let mut statement = conn.prepare(
        "SELECT a.token_address,a.symbol,a.name,MAX(a.price_change_h24),MAX(a.price_usd),
                MAX(a.volume_h24),MAX(a.liquidity_usd),MAX(a.market_cap),MAX(a.holders),
                MAX(a.futures_listed),MIN(a.observed_at),MAX(a.observed_at),
                EXISTS(SELECT 1 FROM pairs p WHERE p.token_address=a.token_address AND p.active=1)
         FROM alpha_universe_snapshots a GROUP BY a.token_address
         ORDER BY MAX(a.price_change_h24) DESC LIMIT ?1",
    )?;
    let rows = statement.query_map([top_n], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, Option<f64>>(3)?,
            row.get::<_, Option<f64>>(4)?,
            row.get::<_, Option<f64>>(5)?,
            row.get::<_, Option<f64>>(6)?,
            row.get::<_, Option<f64>>(7)?,
            row.get::<_, Option<i64>>(8)?,
            row.get::<_, i64>(9)?,
            row.get::<_, String>(10)?,
            row.get::<_, String>(11)?,
            row.get::<_, i64>(12)?,
        ))
    })?;
    let mut movers = Vec::new();
    for row in rows {
        let (
            address,
            symbol,
            name,
            change,
            price,
            volume,
            liquidity,
            market_cap,
            holders,
            futures,
            first,
            last,
            active,
        ) = row?;
        let classification = if active == 1 {
            "monitored"
        } else if futures == 1 {
            "futures_listed_unmonitored"
        } else {
            "not_futures_listed"
        };
        movers.push(json!({
            "token_address":address,"symbol":symbol,"name":name,
            "classification":classification,"futures_listed":futures==1,
            "max_price_change_h24_pct":change,"max_price_usd":price,
            "max_volume_h24_usd":volume,"max_liquidity_usd":liquidity,
            "max_market_cap_usd":market_cap,"max_holders":holders,
            "first_seen_at":first,"last_seen_at":last
        }));
    }
    let (rows, tokens): (i64, i64) = conn.query_row(
        "SELECT count(),count(DISTINCT token_address) FROM alpha_universe_snapshots",
        [],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    let not_listed = movers
        .iter()
        .filter(|mover| mover["classification"] == "not_futures_listed")
        .count();
    let unmonitored = movers
        .iter()
        .filter(|mover| mover["classification"] == "futures_listed_unmonitored")
        .count();
    Ok(json!({
        "mode":"alpha_discovery_audit","source_time_unverified":true,
        "coverage":{"rows":rows,"tokens":tokens,"top_movers":movers.len(),
                    "top_movers_not_futures_listed":not_listed,
                    "top_movers_futures_listed_unmonitored":unmonitored},
        "movers":movers,
        "methods":{
            "storage":"full Binance Alpha BSC list is retained in five-minute buckets before futures filtering",
            "ranking":"top tokens by maximum stored trailing 24h percent change; no future knowledge or execution is claimed",
            "classification":"monitored requires an active pair; futures_listed_unmonitored means listed but no active pair; not_futures_listed was excluded by the contract filter"
        }
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::clients::binance_alpha::AlphaToken;
    use rusqlite::Connection;
    use serde_json::json;

    fn temp_db(tag: &str) -> String {
        let path = std::env::temp_dir().join(format!(
            "tmm-alpha-discovery-{tag}-{}-{}.db",
            std::process::id(),
            chrono::Utc::now().timestamp_micros()
        ));
        path.to_str().unwrap().to_string()
    }

    fn token(address: &str, symbol: &str, price: f64, change: f64) -> AlphaToken {
        AlphaToken {
            address: address.into(),
            symbol: symbol.into(),
            name: symbol.into(),
            raw: json!({
                "price": price.to_string(),
                "percentChange24h": change.to_string(),
                "volume24h": "1000",
                "liquidity": "2000",
                "marketCap": "3000",
                "holders": 12
            }),
        }
    }

    #[test]
    fn records_all_alpha_tokens_and_reuses_one_five_minute_bucket() {
        let path = temp_db("record");
        crate::db::init_schema(&path).unwrap();
        let at = chrono::DateTime::from_timestamp(1_800_000_000, 0).unwrap();
        let inserted = record_snapshots(
            &path,
            &[token("0xa", "A", 1.0, 10.0), token("0xb", "B", 2.0, -5.0)],
            &["0xa".to_string()].into(),
            at,
        )
        .unwrap();
        assert_eq!(inserted, 2);
        let updated = record_snapshots(
            &path,
            &[token("0xa", "A", 1.2, 11.0)],
            &["0xa".to_string()].into(),
            at + chrono::Duration::seconds(60),
        )
        .unwrap();
        assert_eq!(updated, 1);
        let conn = Connection::open(&path).unwrap();
        let (count, price): (i64, f64) = conn
            .query_row("SELECT count(), max(price_usd) FROM alpha_universe_snapshots WHERE token_address='0xa'", [], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap();
        assert_eq!(count, 1);
        assert!((price - 1.2).abs() < 1e-9);
        let raw: String = conn
            .query_row(
                "SELECT raw_json FROM alpha_universe_snapshots WHERE token_address='0xa'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(
            raw, "{}",
            "the audit stores its queryable columns, not a duplicate raw response"
        );
        std::fs::remove_file(&path).unwrap();
    }

    #[test]
    fn audit_ranks_top_movers_and_separates_unlisted_from_monitored() {
        let path = temp_db("audit");
        crate::db::init_schema(&path).unwrap();
        let at = chrono::DateTime::from_timestamp(1_800_000_000, 0).unwrap();
        record_snapshots(
            &path,
            &[
                token("0xhigh", "HIGH", 2.0, 55.0),
                token("0xmonitored", "MON", 1.1, 20.0),
            ],
            &["0xmonitored".to_string()].into(),
            at,
        )
        .unwrap();
        let conn = Connection::open(&path).unwrap();
        conn.execute(
            "INSERT INTO pairs(pair_address,chain_id,token_address,token_symbol,quote_token_address,quote_symbol,token0_address,token1_address,pair_created_at,discovered_at,updated_at,active) VALUES('p','bsc','0xmonitored','MON','','','','','','','',1)",
            [],
        )
        .unwrap();
        let report = audit(&path, 2).unwrap();
        let movers = report["movers"].as_array().unwrap();
        assert_eq!(movers[0]["token_address"], "0xhigh");
        assert_eq!(movers[0]["classification"], "not_futures_listed");
        assert_eq!(movers[1]["classification"], "monitored");
        assert_eq!(report["coverage"]["top_movers_not_futures_listed"], 1);
        std::fs::remove_file(&path).unwrap();
    }
}
