use std::env;

#[derive(Clone, Debug)]
pub struct Config {
    pub database_path: String,
    pub worker_loop_seconds: u64,
    pub max_pairs_per_cycle: usize,
    pub binance_alpha_url: String,
    pub binance_alpha_refresh_minutes: u64,
    pub dexscreener_base_url: String,
    pub focus_score_threshold: i32,
    pub alert_score_threshold: i32,
    pub min_liquidity_usd: f64,
    pub archive_liquidity_usd: f64,
    pub min_volume_h1_usd: f64,
    pub min_buy_count_m5: i32,
    pub min_buy_sell_ratio_m5: f64,
    pub focus_volume_to_liquidity_ratio: f64,
    pub telegram_bot_token: String,
    pub telegram_chat_id: String,
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            database_path: env::var("MONITOR_DATABASE_PATH")
                .unwrap_or_else(|_| "data/monitor.db".into()),
            worker_loop_seconds: env::var("WORKER_LOOP_SECONDS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(15),
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
            dexscreener_base_url: env::var("DEXSCREENER_BASE_URL")
                .unwrap_or_else(|_| "https://api.dexscreener.com/latest/dex".into()),
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
            telegram_bot_token: env::var("TELEGRAM_BOT_TOKEN").unwrap_or_default(),
            telegram_chat_id: env::var("TELEGRAM_CHAT_ID").unwrap_or_default(),
        }
    }
}
