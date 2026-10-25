use std::env;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SignalV2Mode {
    Legacy,
    Shadow,
}

impl SignalV2Mode {
    pub fn parse(value: Option<&str>) -> Self {
        match value {
            Some("shadow") => Self::Shadow,
            None | Some("legacy") => Self::Legacy,
            Some(_) => {
                crate::logging::event(
                    "warn",
                    "signal_v2_invalid_mode",
                    serde_json::json!({"fallback":"legacy"}),
                );
                Self::Legacy
            }
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Legacy => "legacy",
            Self::Shadow => "shadow",
        }
    }
}

#[derive(Clone, Debug)]
pub struct Config {
    pub signal_v2_mode: SignalV2Mode,
    pub worker_loop_seconds: u64,
    pub max_pairs_per_cycle: usize,
    pub binance_alpha_url: String,
    pub binance_alpha_refresh_minutes: u64,
    pub focus_score_threshold: i32,
    pub alert_score_threshold: i32,
    pub min_liquidity_usd: f64,
    pub archive_liquidity_usd: f64,
    pub min_volume_h1_usd: f64,
    pub min_buy_count_m5: i32,
    pub min_buy_sell_ratio_m5: f64,
    pub focus_volume_to_liquidity_ratio: f64,
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            signal_v2_mode: SignalV2Mode::parse(env::var("SIGNAL_V2_MODE").ok().as_deref()),
            worker_loop_seconds: env::var("WORKER_LOOP_SECONDS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(10),
            max_pairs_per_cycle: env::var("MAX_PAIRS_PER_CYCLE")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(80),
            binance_alpha_url: env::var("BINANCE_ALPHA_URL")
                .unwrap_or_else(|_| {
                    "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list".into()
                }),
            binance_alpha_refresh_minutes: env::var("BINANCE_ALPHA_REFRESH_MINUTES")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(1),
            focus_score_threshold: env::var("FOCUS_SCORE_THRESHOLD")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(65),
            alert_score_threshold: env::var("ALERT_SCORE_THRESHOLD")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(78),
            min_liquidity_usd: env::var("MIN_LIQUIDITY_USD")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(15000.0),
            archive_liquidity_usd: env::var("ARCHIVE_LIQUIDITY_USD")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(3000.0),
            min_volume_h1_usd: env::var("MIN_VOLUME_H1_USD")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(15000.0),
            min_buy_count_m5: env::var("MIN_BUY_COUNT_M5")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(8),
            min_buy_sell_ratio_m5: env::var("MIN_BUY_SELL_RATIO_M5")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(1.8),
            focus_volume_to_liquidity_ratio: env::var("FOCUS_VOLUME_TO_LIQUIDITY_RATIO")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(0.25),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::SignalV2Mode;
    #[test]
    fn signal_v2_mode_accepts_only_explicit_shadow() {
        for (input, expected) in [
            (None, "legacy"),
            (Some("legacy"), "legacy"),
            (Some("shadow"), "shadow"),
            (Some("SHADOW"), "legacy"),
            (Some(""), "legacy"),
            (Some("enabled"), "legacy"),
        ] {
            assert_eq!(SignalV2Mode::parse(input).as_str(), expected);
        }
    }
}
