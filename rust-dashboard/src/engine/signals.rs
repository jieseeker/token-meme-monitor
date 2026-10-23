//! Candidate scoring rules for the merged token view.

use super::features::FeatureVector;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeSet;

#[derive(Clone, Debug)]
pub struct SignalConfig {
    pub min_liquidity_usd: f64,
    pub min_volume_h1_usd: f64,
    pub min_buy_count_m5: i32,
    pub min_buy_sell_ratio_m5: f64,
    pub focus_volume_to_liquidity_ratio: f64,
    pub focus_score_threshold: i32,
    pub alert_score_threshold: i32,
    pub strategy_version: String,
}

impl Default for SignalConfig {
    fn default() -> Self {
        Self {
            min_liquidity_usd: 15000.0,
            min_volume_h1_usd: 15000.0,
            min_buy_count_m5: 8,
            min_buy_sell_ratio_m5: 1.8,
            focus_volume_to_liquidity_ratio: 0.25,
            focus_score_threshold: 65,
            alert_score_threshold: 78,
            strategy_version: "v1".into(),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SignalDecision {
    pub observed_at: String,
    pub strategy_version: String,
    pub score: i32,
    pub pair_state: String,
    pub should_alert: bool,
    pub reasons: Vec<String>,
    pub risk_flags: Vec<String>,
}

fn clamp_score(score: i32) -> i32 {
    score.clamp(0, 100)
}

fn metadata_f64(metadata: &Value, key: &str) -> Option<f64> {
    metadata.get(key).and_then(|v| {
        if v.is_null() || v.as_str() == Some("") {
            return None;
        }
        v.as_f64()
            .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
    })
}

fn metadata_bool(metadata: &Value, key: &str) -> bool {
    match metadata.get(key) {
        None => false,
        Some(v) => match v {
            Value::Bool(b) => *b,
            Value::String(s) => matches!(s.trim().to_lowercase().as_str(), "1" | "true" | "yes"),
            Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
            _ => false,
        },
    }
}

/// Evaluate the candidate signal score.
pub fn evaluate(
    features: &FeatureVector,
    observed_at: &str,
    monitor_universe: &str,
    config: &SignalConfig,
    token_metadata: Option<&Value>,
) -> SignalDecision {
    let mut score: i32 = 0;
    let mut reasons: Vec<String> = Vec::new();
    let metadata = token_metadata.cloned().unwrap_or(Value::Null);
    let risk_flags: Vec<String> = features.risk_flags.clone();

    // Rule 1: age window (non-binance_alpha only)
    if monitor_universe != "binance_alpha" {
        if features.age_minutes <= 120.0 {
            score += 10;
            reasons.push("early_age_window".into());
        } else if features.age_minutes <= 360.0 {
            score += 5;
            reasons.push("tradable_age_window".into());
        }
    }

    // Rule 2: liquidity band
    if features.liquidity_usd >= config.min_liquidity_usd && features.liquidity_usd <= 250_000.0 {
        score += 15;
        reasons.push("healthy_liquidity_band".into());
    } else if features.liquidity_usd > 250_000.0 {
        score += 15;
        reasons.push("ample_liquidity".into());
    } else {
        score -= 20;
    }

    // Rule 3: volume
    if features.volume_h1 >= config.min_volume_h1_usd {
        score += 15;
        reasons.push("h1_volume_support".into());
    } else if features.volume_m5 >= config.min_volume_h1_usd * 0.12 {
        score += 6;
        reasons.push("m5_volume_impulse".into());
    }

    // Rule 4: buy dominance
    if features.buys_m5 >= config.min_buy_count_m5
        && features.buy_sell_ratio_m5 >= config.min_buy_sell_ratio_m5
    {
        score += 20;
        reasons.push("m5_buy_dominance".into());
    } else if features.buy_sell_ratio_h1 >= 1.3 && features.buys_h1 >= config.min_buy_count_m5 * 2 {
        score += 12;
        reasons.push("h1_buy_dominance".into());
    }

    // Rule 5: volume/liquidity ratio
    if let Some(v2l) = features.volume_to_liquidity_h1 {
        if v2l >= config.focus_volume_to_liquidity_ratio {
            score += 15;
            reasons.push("volume_to_liquidity_breakout".into());
        } else if v2l >= 0.12 {
            score += 8;
            reasons.push("volume_to_liquidity_support".into());
        }
    }

    // Rule 6: liquidity/FDV balance
    if let Some(l2f) = features.liquidity_to_fdv {
        if (0.04..=0.40).contains(&l2f) {
            score += 10;
            reasons.push("balanced_liquidity_to_fdv".into());
        }
    }

    // Rule 7: price trend
    if features.price_change_h1 > 20.0 && features.price_change_m5 > 0.0 {
        score += 5;
        reasons.push("positive_price_trend".into());
    } else if features.price_change_h1 < -20.0 {
        score -= 5;
    }

    // Rule 10+: binance_alpha specific
    if monitor_universe == "binance_alpha" {
        let alpha_score = metadata_f64(&metadata, "alpha_score");

        // Alpha's token-wide activity can cover trading absent from the sampled DEX pools.
        if features.liquidity_usd < config.min_liquidity_usd
            && !risk_flags.iter().any(|f| f == "liquidity_near_zero")
            && metadata_f64(&metadata, "alpha_liquidity_usd").unwrap_or(0.0)
                >= config.min_liquidity_usd
            && metadata_f64(&metadata, "alpha_volume_h24_usd").unwrap_or(0.0)
                >= config.min_volume_h1_usd * 24.0
        {
            score += 15;
            reasons.push("alpha_activity_support".into());
        }

        if let Some(a) = alpha_score {
            if a >= 100.0 {
                score += 10;
                reasons.push("alpha_hot_score".into());
            } else if a >= 80.0 {
                score += 5;
                reasons.push("alpha_score_support".into());
            }
        }
        if metadata_bool(&metadata, "binance_futures_listed") {
            score += 8;
            reasons.push("binance_futures_listed".into());
        }
    }

    // Risk penalties
    if risk_flags.iter().any(|f| f == "sell_pressure") {
        score -= 8;
    }
    if risk_flags.iter().any(|f| f == "fdv_liquidity_stretched") {
        score -= 8;
    }
    if risk_flags.iter().any(|f| f == "missing_price") {
        score -= 20;
    }

    let score = clamp_score(score);

    // Determine state
    let has_severe_risk = risk_flags.iter().any(|f| {
        let severe = matches!(f.as_str(), "missing_price" | "liquidity_near_zero");
        severe || (monitor_universe != "binance_alpha" && f == "stale_pair")
    });
    let has_structural_blocker = risk_flags.iter().any(|f| f == "fdv_liquidity_stretched");

    let pair_state = if has_severe_risk {
        "archived".to_string()
    } else if score >= config.alert_score_threshold && !has_structural_blocker {
        "alerted".to_string()
    } else if score >= config.focus_score_threshold {
        "focused".to_string()
    } else {
        "watching".to_string()
    };

    let should_alert = pair_state == "alerted" && !has_severe_risk;

    // Deduplicate risk flags (BTreeSet for deterministic order, matching Python's sorted set)
    let flag_set: BTreeSet<String> = risk_flags.into_iter().collect();

    SignalDecision {
        observed_at: observed_at.to_string(),
        strategy_version: config.strategy_version.clone(),
        score,
        pair_state,
        should_alert,
        reasons,
        risk_flags: flag_set.into_iter().collect(),
    }
}
