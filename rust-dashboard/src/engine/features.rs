//! Feature vector construction — ported from Python features.py.
//! Must produce identical results to the Python implementation.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PairSnapshot {
    pub pair_address: String,
    pub token_address: String,
    pub observed_at: String,
    pub pair_created_at: String,
    pub price_usd: Option<f64>,
    pub price_native: Option<f64>,
    pub liquidity_usd: Option<f64>,
    pub fdv: Option<f64>,
    pub market_cap: Option<f64>,
    pub volume_m5: f64,
    pub volume_h1: f64,
    pub volume_h24: f64,
    pub buys_m5: i32,
    pub sells_m5: i32,
    pub buys_h1: i32,
    pub sells_h1: i32,
    pub price_change_m5: f64,
    pub price_change_h1: f64,
    pub price_change_h24: f64,
    pub website_count: i32,
    pub social_count: i32,
    pub boosts_active: i32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FeatureVector {
    pub age_minutes: f64,
    pub liquidity_usd: f64,
    pub fdv: f64,
    pub market_cap: f64,
    pub price_usd: f64,
    pub volume_m5: f64,
    pub volume_h1: f64,
    pub volume_h24: f64,
    pub buys_m5: i32,
    pub sells_m5: i32,
    pub buys_h1: i32,
    pub sells_h1: i32,
    pub tx_count_m5: i32,
    pub tx_count_h1: i32,
    pub buy_sell_ratio_m5: f64,
    pub buy_sell_ratio_h1: f64,
    pub liquidity_to_fdv: Option<f64>,
    pub volume_to_liquidity_h1: Option<f64>,
    pub website_count: i32,
    pub social_count: i32,
    pub boosts_active: i32,
    pub price_change_m5: f64,
    pub price_change_h1: f64,
    pub price_change_h24: f64,
    pub risk_flags: Vec<String>,
}

/// Thresholds mirroring Python's SignalConfig.
#[derive(Clone, Debug)]
pub struct FeatureThresholds {
    pub min_liquidity_usd: f64,
    pub archive_liquidity_usd: f64,
    pub min_buy_count_m5: i32,
    pub max_pair_age_hours: f64,
}

impl Default for FeatureThresholds {
    fn default() -> Self {
        Self {
            min_liquidity_usd: 15000.0,
            archive_liquidity_usd: 3000.0,
            min_buy_count_m5: 8,
            max_pair_age_hours: 24.0,
        }
    }
}

fn parse_iso_minutes(iso: &str) -> f64 {
    // Parse ISO timestamp and return epoch minutes. Handles common formats.
    chrono::DateTime::parse_from_rfc3339(iso)
        .map(|dt| dt.timestamp() as f64 / 60.0)
        .unwrap_or(0.0)
}

fn ratio(buys: i32, sells: i32) -> f64 {
    if buys <= 0 && sells <= 0 {
        return 0.0;
    }
    if sells <= 0 {
        return buys as f64;
    }
    buys as f64 / sells as f64
}

/// Build feature vector from a pair snapshot. Ported from Python build_feature_vector().
pub fn build_feature_vector(
    snapshot: &PairSnapshot,
    thresholds: &FeatureThresholds,
    monitor_universe: &str,
) -> FeatureVector {
    let observed_minutes = parse_iso_minutes(&snapshot.observed_at);
    let created_minutes = parse_iso_minutes(&snapshot.pair_created_at);
    let age_minutes = (observed_minutes - created_minutes).max(0.0);

    let liquidity_usd = snapshot.liquidity_usd.unwrap_or(0.0);
    let fdv = snapshot.fdv.unwrap_or(0.0);
    let market_cap = snapshot.market_cap.unwrap_or(0.0);
    let price_usd = snapshot.price_usd.unwrap_or(0.0);
    let tx_count_m5 = snapshot.buys_m5 + snapshot.sells_m5;
    let tx_count_h1 = snapshot.buys_h1 + snapshot.sells_h1;

    let mut risk_set: BTreeSet<String> = BTreeSet::new();

    if price_usd <= 0.0 {
        risk_set.insert("missing_price".into());
    }
    if liquidity_usd >= thresholds.archive_liquidity_usd
        && liquidity_usd < thresholds.min_liquidity_usd
    {
        risk_set.insert("low_liquidity".into());
    }
    if liquidity_usd < thresholds.archive_liquidity_usd {
        risk_set.insert("liquidity_near_zero".into());
    }
    if monitor_universe != "binance_alpha" && age_minutes > thresholds.max_pair_age_hours * 60.0 {
        risk_set.insert("stale_pair".into());
    }
    if tx_count_m5 < thresholds.min_buy_count_m5 && snapshot.volume_m5 <= 0.0 {
        risk_set.insert("thin_m5_activity".into());
    }
    if (snapshot.sells_m5 as f64) > (snapshot.buys_m5 as f64) * 1.5 && snapshot.sells_m5 >= 5 {
        risk_set.insert("sell_pressure".into());
    }
    if fdv <= 0.0 {
        risk_set.insert("fdv_missing".into());
    } else if liquidity_usd > 0.0 && fdv / liquidity_usd > 25.0 {
        risk_set.insert("fdv_liquidity_stretched".into());
    }

    let liquidity_to_fdv = if liquidity_usd > 0.0 && fdv > 0.0 {
        Some(liquidity_usd / fdv)
    } else {
        None
    };
    let volume_to_liquidity_h1 = if liquidity_usd > 0.0 {
        Some(snapshot.volume_h1 / liquidity_usd)
    } else {
        None
    };

    FeatureVector {
        age_minutes,
        liquidity_usd,
        fdv,
        market_cap,
        price_usd,
        volume_m5: snapshot.volume_m5,
        volume_h1: snapshot.volume_h1,
        volume_h24: snapshot.volume_h24,
        buys_m5: snapshot.buys_m5,
        sells_m5: snapshot.sells_m5,
        buys_h1: snapshot.buys_h1,
        sells_h1: snapshot.sells_h1,
        tx_count_m5,
        tx_count_h1,
        buy_sell_ratio_m5: ratio(snapshot.buys_m5, snapshot.sells_m5),
        buy_sell_ratio_h1: ratio(snapshot.buys_h1, snapshot.sells_h1),
        liquidity_to_fdv,
        volume_to_liquidity_h1,
        website_count: snapshot.website_count,
        social_count: snapshot.social_count,
        boosts_active: snapshot.boosts_active,
        price_change_m5: snapshot.price_change_m5,
        price_change_h1: snapshot.price_change_h1,
        price_change_h24: snapshot.price_change_h24,
        risk_flags: risk_set.into_iter().collect(),
    }
}
