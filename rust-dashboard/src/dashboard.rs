//! Dashboard HTTP server — serves the embedded HTML + JSON API.

use axum::{
    extract::{Path, Query, Request, State},
    http::{header, HeaderValue, StatusCode},
    middleware::{self, Next},
    response::{Html, IntoResponse, Json, Response},
    routing::get,
    Router,
};
use serde::Deserialize;
use std::collections::{HashMap, HashSet, VecDeque};
use std::error::Error as _;
use std::future::Future;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use tower_http::compression::CompressionLayer;

type ResultCache = Arc<tokio::sync::Mutex<HashMap<String, CacheEntry>>>;

struct CacheEntry {
    checked_at: std::time::Instant,
    value: Option<serde_json::Value>,
    loading: bool,
    failures: u32,
}

struct GeckoPacer {
    next: std::time::Instant,
    spacing_secs: u64,
    successes: u8,
    recent_limits: VecDeque<std::time::Instant>,
}

struct GeckoProxy {
    client: reqwest::Client,
    retry_at: tokio::sync::Mutex<std::time::Instant>,
}

fn gecko_proxies() -> Vec<GeckoProxy> {
    let mut seen = HashSet::new();
    std::env::var("GECKOTERMINAL_PROXY_URLS")
        .unwrap_or_default()
        .split(',')
        .map(str::trim)
        .filter(|url| !url.is_empty() && seen.insert((*url).to_string()))
        .filter_map(|url| {
            if !url.starts_with("http://") && !url.starts_with("https://") {
                return None;
            }
            let proxy = reqwest::Proxy::all(url).ok()?;
            let client = reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(8))
                .user_agent("TokenMemeMonitor/0.1")
                .no_proxy()
                .proxy(proxy)
                .build()
                .ok()?;
            Some(GeckoProxy {
                client,
                retry_at: tokio::sync::Mutex::new(std::time::Instant::now()),
            })
        })
        .collect()
}

impl GeckoPacer {
    fn new() -> Self {
        Self {
            next: std::time::Instant::now(),
            spacing_secs: 20,
            successes: 0,
            recent_limits: VecDeque::new(),
        }
    }

    fn cooling_down(&self, now: std::time::Instant) -> bool {
        self.next.saturating_duration_since(now) > std::time::Duration::from_secs(self.spacing_secs)
    }

    fn after_response(
        &mut self,
        status: Option<StatusCode>,
        retry_after: Option<u64>,
        proxy_rate_limits: u8,
    ) -> Option<u64> {
        let now = std::time::Instant::now();
        while self
            .recent_limits
            .front()
            .is_some_and(|time| now.duration_since(*time).as_secs() >= 300)
        {
            self.recent_limits.pop_front();
        }
        let rate_limits =
            proxy_rate_limits + u8::from(status == Some(StatusCode::TOO_MANY_REQUESTS));
        for _ in 0..rate_limits {
            self.recent_limits.push_back(now);
        }
        if rate_limits > 0 {
            self.successes = 0;
            if self.recent_limits.len() >= 2 {
                self.spacing_secs = (self.spacing_secs + 2).min(20);
            }
        } else if status.is_some_and(|status| status.is_success()) {
            self.successes += 1;
            if self.successes >= 5 {
                self.spacing_secs = self.spacing_secs.saturating_sub(1).max(5);
                self.successes = 0;
            }
        }
        if status == Some(StatusCode::TOO_MANY_REQUESTS) {
            // ponytail: cap long provider cooldowns at one hour; persist bans if longer limits become routine.
            let cooldown = retry_after
                .unwrap_or(0)
                .max(self.spacing_secs * 6)
                .clamp(60, 3600);
            self.next = now + std::time::Duration::from_secs(cooldown);
            return Some(cooldown);
        }
        self.next = now + std::time::Duration::from_secs(self.spacing_secs);
        None
    }
}

const INDEX_HTML: &str = include_str!("../static/index.html");
const FAVICON_SVG: &str = include_str!("../static/favicon.svg");
const DEPTH_TTL: std::time::Duration = std::time::Duration::from_secs(120);

#[derive(Clone)]
struct AppState {
    db_path: Arc<String>,
    signal_v2_mode: crate::config::SignalV2Mode,
    signal_v2_settings: crate::engine::signal_v2::Settings,
    http_client: reqwest::Client,
    futures_catalog: Arc<tokio::sync::Mutex<Option<(std::time::Instant, Option<HashSet<String>>)>>>,
    global_oi:
        Arc<tokio::sync::Mutex<Option<(std::time::Instant, HashMap<String, serde_json::Value>)>>>,
    depth_cache: ResultCache,
    oi_cache: ResultCache,
    price_cache: ResultCache,
    whale_cache: ResultCache,
    whale_priority: Arc<tokio::sync::Mutex<Vec<String>>>,
    transfer_cache: ResultCache,
    depth_slots: Arc<tokio::sync::Semaphore>,
    whale_slots: Arc<tokio::sync::Semaphore>,
    gecko_request_gate: Arc<tokio::sync::Semaphore>,
    gecko_next: Arc<tokio::sync::Mutex<GeckoPacer>>,
    gecko_proxies: Arc<Vec<GeckoProxy>>,
    gecko_proxy_cursor: Arc<AtomicUsize>,
}

#[derive(Deserialize)]
struct OverviewParams {
    limit: Option<usize>,
}

#[derive(Deserialize)]
struct SignalV2Params {
    token: Option<String>,
    limit: Option<usize>,
}

#[derive(Deserialize)]
struct StructureParams {
    token: Option<String>,
    limit: Option<usize>,
}

fn signal_v2_diagnostic(
    path: &str,
    params: SignalV2Params,
    mode: crate::config::SignalV2Mode,
    settings: &crate::engine::signal_v2::Settings,
    now: i64,
) -> Result<serde_json::Value, (StatusCode, Json<serde_json::Value>)> {
    let limit = params.limit.unwrap_or(100);
    if !(1..=500).contains(&limit)
        || params
            .token
            .as_ref()
            .is_some_and(|token| !valid_hex(token, 42))
    {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error":"Invalid token or limit (1..500)"})),
        ));
    }
    if !crate::engine::signal_v2_store::settings_valid(settings) {
        return Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error":"Invalid shadow parameters"})),
        ));
    }
    let token = params.token.map(|token| token.to_ascii_lowercase());
    let rows = crate::engine::signal_v2_store::summaries(path, token.as_deref(), limit, now)
        .map_err(|_| {
            crate::logging::event(
                "warn",
                "signal_v2_diagnostic_failed",
                serde_json::json!({"reason":"state_read_failed"}),
            );
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error":"Signal diagnostics unavailable"})),
            )
        })?;
    Ok(
        serde_json::json!({"mode":mode.as_str(),"source_time_unverified":true,"experiment_version":crate::engine::signal_v2_store::EXPERIMENT_VERSION,"settings":settings,"summaries":rows}),
    )
}

async fn signal_v2_diagnostics(
    State(state): State<AppState>,
    Query(params): Query<SignalV2Params>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let path = state.db_path.as_ref().clone();
    let mode = state.signal_v2_mode;
    let settings = state.signal_v2_settings.clone();
    tokio::task::spawn_blocking(move || {
        signal_v2_diagnostic(
            &path,
            params,
            mode,
            &settings,
            chrono::Utc::now().timestamp(),
        )
    })
    .await
    .map_err(axum_error)?
    .map(Json)
}

#[cfg(test)]
mod signal_v2_tests {
    use super::*;
    #[test]
    fn signal_v2_diagnostic_rejects_invalid_query_before_database_access() {
        for (token, limit) in [
            (Some("bad".into()), Some(1)),
            (None, Some(0)),
            (None, Some(501)),
        ] {
            let result = signal_v2_diagnostic(
                "/no-such-database",
                SignalV2Params { token, limit },
                crate::config::SignalV2Mode::Legacy,
                &crate::engine::signal_v2::Settings::default(),
                100,
            );
            assert_eq!(result.unwrap_err().0, StatusCode::BAD_REQUEST);
        }
    }
    #[test]
    fn signal_v2_diagnostic_rejects_invalid_current_parameters() {
        let settings = crate::engine::signal_v2::Settings {
            min_volume_h1_usd: f64::NAN,
            ..Default::default()
        };
        let result = signal_v2_diagnostic(
            "/no-such-database",
            SignalV2Params {
                token: None,
                limit: Some(1),
            },
            crate::config::SignalV2Mode::Shadow,
            &settings,
            100,
        );
        assert_eq!(
            result.unwrap_err().1 .0["error"],
            "Invalid shadow parameters"
        );
    }
    #[test]
    fn signal_v2_diagnostic_reports_mode_and_unverified_source() {
        let path = std::env::temp_dir().join(format!(
            "tmm-diagnostic-{}-{}.db",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let path = path.to_str().unwrap();
        crate::db::init_schema(path).unwrap();
        let value = signal_v2_diagnostic(
            path,
            SignalV2Params {
                token: Some(format!("0x{}", "a".repeat(40))),
                limit: Some(1),
            },
            crate::config::SignalV2Mode::Shadow,
            &crate::engine::signal_v2::Settings::default(),
            100,
        )
        .unwrap();
        assert_eq!(value["settings"]["min_volume_h1_usd"], 15000.0);
        assert_eq!(value["experiment_version"], "signal_v2_shadow_v1");
        assert_eq!(value["mode"], "shadow");
        assert_eq!(value["source_time_unverified"], true);
        assert_eq!(value["summaries"], serde_json::json!([]));
    }
}

pub async fn serve(db_path: &str) {
    let config = crate::config::Config::from_env();
    let proxies = gecko_proxies();
    crate::logging::event(
        "info",
        "whale_proxy_pool_configured",
        serde_json::json!({"count":proxies.len()}),
    );
    let state = AppState {
        db_path: Arc::new(db_path.to_string()),
        signal_v2_mode: config.signal_v2_mode,
        signal_v2_settings: crate::engine::signal_v2::Settings {
            min_volume_h1_usd: config.min_volume_h1_usd,
            focus_score_threshold: config.focus_score_threshold,
        },
        http_client: reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(8))
            .user_agent("TokenMemeMonitor/0.1")
            .no_proxy()
            .build()
            .expect("failed to build upstream HTTP client"),
        futures_catalog: Arc::new(tokio::sync::Mutex::new(None)),
        global_oi: Arc::new(tokio::sync::Mutex::new(None)),
        depth_cache: Arc::new(tokio::sync::Mutex::new(HashMap::new())),
        oi_cache: Arc::new(tokio::sync::Mutex::new(HashMap::new())),
        price_cache: Arc::new(tokio::sync::Mutex::new(HashMap::new())),
        whale_cache: Arc::new(tokio::sync::Mutex::new(HashMap::new())),
        whale_priority: Arc::new(tokio::sync::Mutex::new(Vec::new())),
        transfer_cache: Arc::new(tokio::sync::Mutex::new(HashMap::new())),
        depth_slots: Arc::new(tokio::sync::Semaphore::new(4)),
        whale_slots: Arc::new(tokio::sync::Semaphore::new(2)),
        gecko_request_gate: Arc::new(tokio::sync::Semaphore::new(1)),
        gecko_next: Arc::new(tokio::sync::Mutex::new(GeckoPacer::new())),
        gecko_proxies: Arc::new(proxies),
        gecko_proxy_cursor: Arc::new(AtomicUsize::new(0)),
    };

    let path = db_path.to_string();
    match tokio::task::spawn_blocking(move || {
        crate::db::init_schema(&path)?;
        crate::db::load_detail_cache(&path)
    })
    .await
    {
        Ok(Ok(rows)) => {
            for (kind, key, value, at) in rows {
                let age = chrono::DateTime::parse_from_rfc3339(&at)
                    .ok()
                    .and_then(|at| {
                        (chrono::Utc::now() - at.with_timezone(&chrono::Utc))
                            .to_std()
                            .ok()
                    })
                    .unwrap_or_default();
                let cache = match kind.as_str() {
                    "oi" => &state.oi_cache,
                    "depth" => &state.depth_cache,
                    "whales" => &state.whale_cache,
                    _ => continue,
                };
                cache.lock().await.insert(
                    key,
                    CacheEntry {
                        checked_at: std::time::Instant::now()
                            .checked_sub(age)
                            .unwrap_or_else(std::time::Instant::now),
                        value: Some(value),
                        loading: false,
                        failures: 0,
                    },
                );
            }
        }
        error => crate::logging::event(
            "error",
            "detail_cache_restore_failed",
            serde_json::json!({"error":format!("{error:?}")}),
        ),
    }

    let app = Router::new()
        .route("/", get(index))
        .route("/favicon.svg", get(favicon))
        .route("/api/overview", get(overview))
        .route("/api/tokens", get(tokens))
        .route("/api/structure", get(structure))
        .route("/api/early-warning", get(early_warning))
        .route("/api/token/{token}/summary", get(token_summary))
        .route("/api/status", get(status))
        .route("/api/signal-v2", get(signal_v2_diagnostics))
        .route("/api/health", get(health_check))
        .route("/api/token/{pair}/snapshots", get(token_snapshots))
        .route("/api/token/{pair}/signals", get(token_signals))
        .route("/api/token/{pair}/oi", get(token_oi))
        .route("/api/token/{pair}/prices", get(token_prices))
        .route("/api/token/{pair}/depth", get(token_depth))
        .route("/api/token/{pair}/whales", get(token_whales))
        .route("/api/token/{pair}/transfers", get(token_transfers))
        .with_state(state.clone())
        .layer(CompressionLayer::new())
        .layer(middleware::from_fn(log_http_failure));

    let port: u16 = std::env::var("DASHBOARD_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8501);

    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{}", port))
        .await
        .unwrap_or_else(|e| {
            crate::logging::event(
                "error",
                "dashboard_bind_failed",
                serde_json::json!({"port":port,"error":e.to_string()}),
            );
            panic!("failed to bind port: {e}")
        });

    crate::logging::event(
        "info",
        "dashboard_started",
        serde_json::json!({"port":port}),
    );
    tokio::spawn(warm_oi(state.clone()));
    tokio::spawn(warm_depth(state.clone()));
    tokio::spawn(warm_whales(state.clone()));
    if let Err(e) = axum::serve(listener, app).await {
        crate::logging::event(
            "error",
            "dashboard_serve_failed",
            serde_json::json!({"error":e.to_string()}),
        );
    }
}

async fn log_http_failure(request: Request, next: Next) -> Response {
    let method = request.method().to_string();
    let path = request.uri().path().to_owned();
    let start = std::time::Instant::now();
    let response = next.run(request).await;
    if response.status().is_server_error() {
        crate::logging::event(
            "error",
            "http_request_failed",
            serde_json::json!({"method":method,"path":path,"status":response.status().as_u16(),"elapsed_ms":start.elapsed().as_millis()}),
        );
    }
    response
}

async fn cached_or_pending(
    cache: &ResultCache,
    key: &str,
    ttl: std::time::Duration,
) -> (serde_json::Value, bool) {
    let mut entries = cache.lock().await;
    let entry = entries
        .entry(key.to_string())
        .or_insert_with(|| CacheEntry {
            checked_at: std::time::Instant::now(),
            value: None,
            loading: false,
            failures: 0,
        });
    let start = cache_entry_due(entry, ttl);
    if start {
        entry.loading = true;
    }
    (
        entry
            .value
            .clone()
            .unwrap_or_else(|| serde_json::json!({"pending":true})),
        start,
    )
}

fn cache_entry_due(entry: &CacheEntry, ttl: std::time::Duration) -> bool {
    let retry = if entry.failures > 0 {
        std::time::Duration::from_secs((30_u64 << entry.failures.min(5)).min(900))
    } else {
        ttl
    };
    !entry.loading && (entry.value.is_none() || entry.checked_at.elapsed() >= retry)
}

async fn cache_due(cache: &ResultCache, key: &str, ttl: std::time::Duration) -> bool {
    cache
        .lock()
        .await
        .get(key)
        .is_none_or(|entry| cache_entry_due(entry, ttl))
}

fn whale_ttl(priority: bool) -> std::time::Duration {
    std::time::Duration::from_secs(if priority { 120 } else { 180 })
}

fn whale_high_limit(limit: usize, has_secondary: bool, priority_due: usize) -> usize {
    limit - usize::from(has_secondary && priority_due < limit)
}

async fn store_result(cache: &ResultCache, key: String, value: serde_json::Value) {
    let mut entries = cache.lock().await;
    let previous = entries.get(&key);
    let failed = value["available"] == false;
    let failures = if failed {
        previous.map_or(1, |entry| entry.failures.saturating_add(1))
    } else {
        0
    };
    let value = if failed
        && previous
            .and_then(|entry| entry.value.as_ref())
            .is_some_and(|old| {
                old["available"] == true
                    && old["calculated_at"]
                        .as_str()
                        .and_then(|at| chrono::DateTime::parse_from_rfc3339(at).ok())
                        .is_some_and(|at| {
                            let age = chrono::Utc::now() - at.with_timezone(&chrono::Utc);
                            age >= chrono::Duration::zero() && age <= chrono::Duration::hours(1)
                        })
            }) {
        let mut old = previous.unwrap().value.as_ref().unwrap().clone();
        old["stale"] = serde_json::json!(true);
        old["last_error"] = value["reason"].clone();
        old
    } else {
        value
    };
    entries.insert(
        key,
        CacheEntry {
            checked_at: std::time::Instant::now(),
            value: Some(value),
            loading: false,
            failures,
        },
    );
}

async fn persist_success(
    state: &AppState,
    kind: &'static str,
    key: &str,
    value: &serde_json::Value,
) {
    if value["available"] != true {
        return;
    }
    let path = state.db_path.as_ref().clone();
    let saved_key = key.to_string();
    let value = value.clone();
    match tokio::task::spawn_blocking(move || {
        crate::db::save_detail_cache(&path, kind, &saved_key, &value)
    })
    .await
    {
        Ok(Ok(())) => {}
        error => crate::logging::event(
            "error",
            "detail_cache_write_failed",
            serde_json::json!({"kind":kind,"key":key,"error":format!("{error:?}")}),
        ),
    }
}

fn valid_hex(value: &str, len: usize) -> bool {
    value.len() == len
        && value.starts_with("0x")
        && value[2..].bytes().all(|b| b.is_ascii_hexdigit())
}

fn whale_trades(body: &serde_json::Value, token: &str) -> Vec<serde_json::Value> {
    let now = chrono::Utc::now();
    body["data"].as_array().into_iter().flatten().filter_map(|row| {
        let item = &row["attributes"];
        if item["is_anomalous"] != false { return None; }
        let usd = item["volume_in_usd"].as_str()?.parse::<f64>().ok()?;
        if !usd.is_finite() || usd <= 10_000.0 { return None; }
        let at = chrono::DateTime::parse_from_rfc3339(item["block_timestamp"].as_str()?).ok()?;
        if at < now - chrono::Duration::hours(24) || at > now + chrono::Duration::minutes(1) { return None; }
        let from = item["from_token_address"].as_str()?.eq_ignore_ascii_case(token);
        let to = item["to_token_address"].as_str()?.eq_ignore_ascii_case(token);
        let side = match (from, to) { (true, false) => "sell", (false, true) => "buy", _ => return None };
        let hash = item["tx_hash"].as_str()?;
        let wallet = item["tx_from_address"].as_str()?;
        if !valid_hex(hash, 66) || !valid_hex(wallet, 42) { return None; }
        Some(serde_json::json!({"side":side,"usd":usd,"at":at.to_rfc3339(),"hash":hash,"wallet":wallet}))
    }).collect()
}

fn large_transfers(body: &serde_json::Value, token: &str) -> Vec<serde_json::Value> {
    let now = chrono::Utc::now().timestamp();
    body["operations"].as_array().into_iter().flatten().filter_map(|item| {
        if item["type"] != "transfer" || !item["tokenInfo"]["address"].as_str()?.eq_ignore_ascii_case(token) { return None; }
        let at = item["timestamp"].as_i64()?;
        if at < now - 86_400 || at > now + 60 { return None; }
        let decimals = item["tokenInfo"]["decimals"].as_str()?.parse::<i32>().ok()?;
        if !(0..=36).contains(&decimals) { return None; }
        let raw = item["value"].as_str()?;
        if raw.is_empty() || !raw.bytes().all(|b| b.is_ascii_digit()) { return None; }
        let amount = raw.parse::<f64>().ok()? / 10_f64.powi(decimals);
        if !amount.is_finite() || amount <= 1_000_000.0 { return None; }
        let from = item["from"].as_str()?;
        let to = item["to"].as_str()?;
        let hash = item["transactionHash"].as_str()?;
        if !valid_hex(from, 42) || !valid_hex(to, 42) || !valid_hex(hash, 66)
            || from.eq_ignore_ascii_case("0x0000000000000000000000000000000000000000")
            || to.eq_ignore_ascii_case("0x0000000000000000000000000000000000000000") { return None; }
        let at = chrono::DateTime::from_timestamp(at, 0)?;
        Some(serde_json::json!({"amount":amount,"from":from,"to":to,"hash":hash,"at":at.to_rfc3339()}))
    }).collect()
}

fn ranked_oi_pairs(rows: &[serde_json::Value]) -> Vec<(String, String)> {
    let mut positions: HashMap<String, usize> = HashMap::new();
    let mut ranked: Vec<(i64, f64, String, String)> = Vec::new();
    let now = chrono::Utc::now();
    for row in rows {
        let mut row = row.clone();
        expire_short_signal(&mut row, now);
        let score = row["short_momentum_score"]
            .as_i64()
            .or_else(|| row["signal_score"].as_i64())
            .unwrap_or(0);
        let liquidity = row["liquidity_usd"].as_f64().unwrap_or(0.0);
        if let (Some(token), Some(pair)) =
            (row["token_address"].as_str(), row["pair_address"].as_str())
        {
            let token = token.to_ascii_lowercase();
            if let Some(&index) = positions.get(&token) {
                if score > ranked[index].0 {
                    ranked[index].0 = score;
                }
                if liquidity > ranked[index].1 {
                    ranked[index].1 = liquidity;
                    ranked[index].3 = pair.to_string();
                }
            } else {
                positions.insert(token.clone(), ranked.len());
                ranked.push((score, liquidity, token, pair.to_string()));
            }
        }
    }
    ranked.retain(|(score, _, _, _)| *score > 0);
    ranked.sort_by(|a, b| b.0.cmp(&a.0));
    ranked
        .into_iter()
        .map(|(_, _, token, pair)| (token, pair))
        .collect()
}

fn oi_warm_batch(rows: &[serde_json::Value], next_other: &mut usize) -> Vec<String> {
    let ranked = ranked_oi_pairs(rows);
    let mut batch: Vec<String> = ranked
        .iter()
        .take(50)
        .map(|(_, pair)| pair.clone())
        .collect();
    let other = &ranked[ranked.len().min(50)..];
    if !other.is_empty() {
        let count = other.len().min(50);
        for offset in 0..count {
            batch.push(other[(*next_other + offset) % other.len()].1.clone());
        }
        *next_other = (*next_other + count) % other.len();
    }
    batch
}

fn depth_warm_candidates(rows: &[serde_json::Value]) -> (Vec<String>, Vec<String>, Vec<String>) {
    let visible: Vec<(String, bool)> = list_overview(rows.to_vec())
        .into_iter()
        .take(50)
        .filter_map(|row| {
            Some((
                row["token_address"].as_str()?.to_string(),
                row["indicator_score"].as_f64().unwrap_or(0.0) > 60.0,
            ))
        })
        .collect();
    let visible_set: HashSet<&str> = visible.iter().map(|(token, _)| token.as_str()).collect();
    let mut by_token: HashMap<String, Vec<(String, f64)>> = HashMap::new();
    for row in rows {
        let (Some(token), Some(pair)) =
            (row["token_address"].as_str(), row["pair_address"].as_str())
        else {
            continue;
        };
        let token = token.to_ascii_lowercase();
        let liquidity = row["liquidity_usd"].as_f64().unwrap_or(0.0);
        let price = row["price_usd"].as_f64().unwrap_or(0.0);
        // ponytail: the $10 floor skips background work on dust pools; lower it if micro-liquidity pools become a monitored target.
        if visible_set.contains(token.as_str())
            && liquidity.is_finite()
            && liquidity >= 10.0
            && price.is_finite()
            && price > 0.0
        {
            by_token
                .entry(token)
                .or_default()
                .push((pair.to_ascii_lowercase(), liquidity));
        }
    }
    let (mut high, mut regular, mut cold) = (Vec::new(), Vec::new(), Vec::new());
    for (token, priority) in visible {
        let Some(mut pools) = by_token.remove(&token) else {
            continue;
        };
        pools.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        let total: f64 = pools.iter().map(|(_, liquidity)| liquidity).sum();
        let mut covered = 0.0;
        for (pair, liquidity) in pools {
            if covered < total * 0.95 {
                covered += liquidity;
                if priority {
                    high.push(pair);
                } else {
                    regular.push(pair);
                }
            } else {
                cold.push(pair);
            }
        }
    }
    (high, regular, cold)
}

fn whale_warm_batch(
    rows: &[serde_json::Value],
    next_high: usize,
    next_low: usize,
) -> (Vec<String>, usize, usize) {
    let listed = list_overview(rows.to_vec());
    let pair_by_token: HashMap<String, String> = ranked_oi_pairs(rows)
        .into_iter()
        .map(|(token, pair)| (token.to_ascii_lowercase(), pair))
        .collect();
    let primary: Vec<_> = listed
        .iter()
        .take(50)
        .filter_map(|row| {
            let token = row["token_address"].as_str()?;
            Some((
                row["indicator_score"].as_f64().unwrap_or(0.0),
                pair_by_token.get(token)?.clone(),
            ))
        })
        .collect();
    let primary_set: HashSet<&str> = primary.iter().map(|(_, pair)| pair.as_str()).collect();
    let (high_major, regular_major, _) = depth_warm_candidates(rows);
    let secondary: Vec<_> = high_major
        .into_iter()
        .chain(regular_major)
        .filter(|pair| !primary_set.contains(pair.as_str()))
        .collect();
    let hot_count = primary
        .iter()
        .take_while(|(score, _)| *score > 60.0)
        .count();
    let mut batch: Vec<_> = primary
        .iter()
        .take(hot_count)
        .map(|(_, pair)| pair.clone())
        .collect();
    let rotating_high = &primary[hot_count..];
    batch.extend((0..rotating_high.len()).map(|offset| {
        rotating_high[(next_high + offset) % rotating_high.len()]
            .1
            .clone()
    }));
    batch.extend(
        (0..secondary.len()).map(|offset| secondary[(next_low + offset) % secondary.len()].clone()),
    );
    (batch, hot_count, rotating_high.len())
}

fn sort_whale_priority(pairs: &mut [String], cache: &HashMap<String, CacheEntry>) {
    pairs.sort_by_cached_key(|pair| {
        cache
            .get(pair)
            .and_then(|entry| entry.value.as_ref())
            .filter(|value| value["available"] == true)
            .and_then(|value| value["calculated_at"].as_str())
            .and_then(|at| chrono::DateTime::parse_from_rfc3339(at).ok())
    });
}

async fn warm_oi(state: AppState) {
    // ponytail: serial warmup bounds public API traffic; add provider-specific concurrency only if full rotation is too slow.
    let mut next_other = 0;
    loop {
        let started = std::time::Instant::now();
        for cache in [
            &state.oi_cache,
            &state.depth_cache,
            &state.price_cache,
            &state.whale_cache,
            &state.transfer_cache,
        ] {
            cache.lock().await.retain(|_, entry| {
                entry.loading || entry.checked_at.elapsed() < std::time::Duration::from_secs(3600)
            });
        }
        let path = state.db_path.as_ref().clone();
        match tokio::task::spawn_blocking(move || crate::db::overview(&path, 5000)).await {
            Ok(Ok(rows)) => {
                let pairs = oi_warm_batch(&rows, &mut next_other);
                let selected = pairs.len();
                let mut refreshed = 0;
                for pair in pairs {
                    if !cache_due(&state.oi_cache, &pair, std::time::Duration::from_secs(60)).await
                    {
                        continue;
                    }
                    let _ = token_oi(State(state.clone()), Path(pair.clone())).await;
                    wait_for_result(&state.oi_cache, &pair).await;
                    refreshed += 1;
                }
                crate::logging::event(
                    "info",
                    "oi_warm_batch_completed",
                    serde_json::json!({"selected":selected,"refreshed":refreshed,"elapsed_ms":started.elapsed().as_millis()}),
                );
            }
            error => crate::logging::event(
                "error",
                "oi_warm_batch_failed",
                serde_json::json!({"error":format!("{error:?}")}),
            ),
        }
        tokio::time::sleep(std::time::Duration::from_secs(30)).await;
    }
}

async fn warm_depth(state: AppState) {
    let (mut next_regular, mut next_cold) = (0, 0);
    loop {
        let started = std::time::Instant::now();
        let path = state.db_path.as_ref().clone();
        match tokio::task::spawn_blocking(move || crate::db::overview(&path, 5000)).await {
            Ok(Ok(rows)) => {
                let (high, regular, cold) = depth_warm_candidates(&rows);
                let high_candidates = high.len();
                let hot_candidates = high.len() + regular.len();
                let high_tokens: HashSet<String> = list_overview(rows.clone())
                    .into_iter()
                    .filter(|row| row["indicator_score"].as_f64().unwrap_or(0.0) > 60.0)
                    .filter_map(|row| row["token_address"].as_str().map(str::to_string))
                    .collect();
                let high_main: Vec<String> = ranked_oi_pairs(&rows)
                    .into_iter()
                    .filter(|(token, _)| high_tokens.contains(&token.to_ascii_lowercase()))
                    .map(|(_, pair)| pair.to_ascii_lowercase())
                    .collect();
                let mut due = Vec::new();
                for pair in high {
                    if cache_due(&state.depth_cache, &pair, DEPTH_TTL).await {
                        due.push(pair);
                    }
                }
                let high_refreshed = due.len();
                // ponytail: cap regular work at 12 pools so high-score pools return quickly; tune against measured RPC latency.
                let (regular_due, regular_scanned) = rotating_due(
                    &state.depth_cache,
                    &regular,
                    &mut next_regular,
                    12,
                    DEPTH_TTL,
                )
                .await;
                let regular_refreshed = regular_due.len();
                due.extend(regular_due);
                let (cold_due, cold_scanned) = rotating_due(
                    &state.depth_cache,
                    &cold,
                    &mut next_cold,
                    2,
                    std::time::Duration::from_secs(300),
                )
                .await;
                let cold_selected = cold_due.len();
                due.extend(cold_due);
                let refreshed = due.len();
                let worker_state = state.clone();
                run_depth_jobs(due, move |pair| {
                    let state = worker_state.clone();
                    async move {
                        let _ = token_depth(State(state.clone()), Path(pair.clone())).await;
                        wait_for_result(&state.depth_cache, &pair).await;
                    }
                })
                .await;
                let now = chrono::Utc::now();
                let entries = state.depth_cache.lock().await;
                let mut high_main_missing = 0;
                let mut high_main_over_5m = 0;
                let mut high_main_oldest_seconds = 0;
                for pair in &high_main {
                    let age = entries
                        .get(pair)
                        .and_then(|entry| entry.value.as_ref())
                        .and_then(|value| {
                            if value["available"] != true {
                                return None;
                            }
                            value["calculated_at"]
                                .as_str()?
                                .parse::<chrono::DateTime<chrono::FixedOffset>>()
                                .ok()
                        })
                        .map(|at| {
                            (now - at.with_timezone(&chrono::Utc)).num_seconds().max(0) as u64
                        });
                    if let Some(age) = age {
                        high_main_over_5m += usize::from(age > 300);
                        high_main_oldest_seconds = high_main_oldest_seconds.max(age);
                    } else {
                        high_main_missing += 1;
                    }
                }
                crate::logging::event(
                    "info",
                    "depth_warm_batch_completed",
                    serde_json::json!({"selected":high_candidates+regular_scanned+cold_scanned,"hot_candidates":hot_candidates,"high_candidates":high_candidates,"regular_scanned":regular_scanned,"regular_selected":regular_refreshed,"cold_scanned":cold_scanned,"cold_selected":cold_selected,"refreshed":refreshed,"high_refreshed":high_refreshed,"regular_refreshed":regular_refreshed,"high_main_total":high_main.len(),"high_main_missing":high_main_missing,"high_main_over_5m":high_main_over_5m,"high_main_oldest_seconds":high_main_oldest_seconds,"elapsed_ms":started.elapsed().as_millis()}),
                );
            }
            error => crate::logging::event(
                "error",
                "depth_warm_batch_failed",
                serde_json::json!({"error":format!("{error:?}")}),
            ),
        }
        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
    }
}

async fn rotating_due(
    cache: &ResultCache,
    pairs: &[String],
    next: &mut usize,
    limit: usize,
    ttl: std::time::Duration,
) -> (Vec<String>, usize) {
    let mut due = Vec::new();
    if pairs.is_empty() || limit == 0 {
        return (due, 0);
    }
    for offset in 0..pairs.len() {
        let pair = &pairs[(*next + offset) % pairs.len()];
        if cache_due(cache, pair, ttl).await {
            due.push(pair.clone());
            if due.len() == limit {
                *next = (*next + offset + 1) % pairs.len();
                return (due, offset + 1);
            }
        }
    }
    (due, pairs.len())
}

async fn run_depth_jobs<F, Fut>(pairs: Vec<String>, work: F)
where
    F: Fn(String) -> Fut + Clone + Send + 'static,
    Fut: Future<Output = ()> + Send + 'static,
{
    let mut jobs = tokio::task::JoinSet::new();
    for pair in pairs {
        let work = work.clone();
        jobs.spawn(async move { work(pair).await });
        if jobs.len() >= 3 {
            jobs.join_next().await;
        }
    }
    while jobs.join_next().await.is_some() {}
}

async fn warm_whales(state: AppState) {
    // ponytail: visible secondary pools rotate slowly under the provider quota; full five-minute coverage needs a higher quota.
    let (mut next_high, mut next_low) = (0, 0);
    loop {
        let Ok(pacer) = state.gecko_next.try_lock() else {
            tokio::time::sleep(std::time::Duration::from_secs(5)).await;
            continue;
        };
        let spacing = pacer.spacing_secs;
        let now = std::time::Instant::now();
        let cooldown = pacer.next.saturating_duration_since(now);
        let cooling_down = pacer.cooling_down(now);
        drop(pacer);
        if cooling_down {
            tokio::time::sleep(cooldown).await;
            continue;
        }
        if state.whale_slots.available_permits() == 0 {
            tokio::time::sleep(std::time::Duration::from_secs(5)).await;
            continue;
        }
        let started = std::time::Instant::now();
        let budget = std::time::Duration::from_secs(150);
        let path = state.db_path.as_ref().clone();
        match tokio::task::spawn_blocking(move || crate::db::overview(&path, 5000)).await {
            Ok(Ok(rows)) => {
                let limit = (120 / (spacing + 5)).clamp(3, 5) as usize;
                let (mut pairs, hot_count, high_count) =
                    whale_warm_batch(&rows, next_high, next_low);
                sort_whale_priority(&mut pairs[..hot_count], &*state.whale_cache.lock().await);
                *state.whale_priority.lock().await = pairs[..hot_count].to_vec();
                let low_count = pairs.len() - hot_count - high_count;
                let candidates = pairs.len();
                let high_end = hot_count + high_count;
                let mut priority_due = 0;
                for pair in pairs.iter().take(hot_count) {
                    priority_due +=
                        usize::from(cache_due(&state.whale_cache, pair, whale_ttl(true)).await);
                }
                let high_limit = whale_high_limit(limit, low_count > 0, priority_due);
                let mut selected = 0;
                let mut refreshed = 0;
                let (mut high_refreshed, mut low_refreshed) = (0, 0);
                let (mut visited_high, mut visited_low) = (0, 0);
                let mut stop_reason = "done";
                for (index, pair) in pairs.into_iter().enumerate() {
                    if refreshed >= limit {
                        stop_reason = "limit";
                        break;
                    }
                    if started.elapsed() >= budget {
                        stop_reason = "budget";
                        break;
                    }
                    let low = index >= high_end;
                    if !low && high_refreshed >= high_limit {
                        continue;
                    }
                    if cache_due(&state.whale_cache, &pair, whale_ttl(index < hot_count)).await {
                        let _ = token_whales(State(state.clone()), Path(pair.clone())).await;
                        if cache_due(&state.whale_cache, &pair, whale_ttl(index < hot_count)).await
                        {
                            stop_reason = "slots_busy";
                            break;
                        }
                        let remaining = budget.saturating_sub(started.elapsed());
                        if tokio::time::timeout(
                            remaining,
                            wait_for_result(&state.whale_cache, &pair),
                        )
                        .await
                        .is_err()
                        {
                            stop_reason = "timeout";
                            break;
                        }
                        refreshed += 1;
                        if low {
                            low_refreshed += 1;
                        } else {
                            high_refreshed += 1;
                        }
                        if state
                            .gecko_next
                            .try_lock()
                            .is_ok_and(|pacer| pacer.cooling_down(std::time::Instant::now()))
                        {
                            stop_reason = "cooldown";
                            break;
                        }
                    }
                    selected += 1;
                    if index >= hot_count && !low {
                        visited_high += 1;
                    } else if low {
                        visited_low += 1;
                    }
                }
                if high_count > 0 {
                    next_high = (next_high + visited_high) % high_count;
                }
                if low_count > 0 {
                    next_low = (next_low + visited_low) % low_count;
                }
                let priority = state.whale_priority.lock().await.clone();
                let whale_entries = state.whale_cache.lock().await;
                let (priority_over_5m, priority_missing) =
                    priority_freshness(&*whale_entries, &priority, chrono::Utc::now());
                drop(whale_entries);
                crate::logging::event(
                    "info",
                    "whale_warm_batch_completed",
                    serde_json::json!({"candidates":candidates,"selected":selected,"refreshed":refreshed,"high_refreshed":high_refreshed,"low_refreshed":low_refreshed,"priority_total":priority.len(),"priority_over_5m":priority_over_5m,"priority_missing":priority_missing,"elapsed_ms":started.elapsed().as_millis(),"spacing_seconds":state.gecko_next.try_lock().map_or(spacing, |pacer| pacer.spacing_secs),"stop_reason":stop_reason}),
                );
            }
            error => crate::logging::event(
                "error",
                "whale_warm_batch_failed",
                serde_json::json!({"error":format!("{error:?}")}),
            ),
        }
        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
    }
}

async fn wait_for_result(cache: &ResultCache, pair: &str) {
    // Wait for the spawned refresh before advancing the bounded warmup queue.
    while cache
        .lock()
        .await
        .get(pair)
        .is_some_and(|entry| entry.loading)
    {
        tokio::time::sleep(std::time::Duration::from_millis(250)).await;
    }
}

async fn index() -> Html<&'static str> {
    Html(INDEX_HTML)
}

async fn favicon() -> impl IntoResponse {
    (
        [(
            header::CONTENT_TYPE,
            HeaderValue::from_static("image/svg+xml"),
        )],
        FAVICON_SVG,
    )
}

fn oi_from_history(body: &serde_json::Value, symbol: &str) -> Option<serde_json::Value> {
    let mut rows: Vec<(i64, f64, f64)> = body
        .as_array()?
        .iter()
        .filter_map(|row| {
            if row.get("symbol")?.as_str()? != symbol {
                return None;
            }
            let at = row.get("timestamp")?.as_i64()?;
            let value = row
                .get("sumOpenInterestValue")?
                .as_str()?
                .parse::<f64>()
                .ok()?;
            let quantity = row.get("sumOpenInterest")?.as_str()?.parse::<f64>().ok()?;
            (at > 0 && value.is_finite() && value >= 0.0 && quantity.is_finite() && quantity >= 0.0)
                .then_some((at, value, quantity))
        })
        .collect();
    rows.sort_unstable_by_key(|row| row.0);
    let &(at, value, quantity) = rows.last()?;
    let change = |hours: i64| {
        rows.iter()
            .find(|row| row.0 == at - hours * 3_600_000 && row.2 > 0.0)
            .map(|row| (quantity / row.2 - 1.0) * 100.0)
    };
    Some(serde_json::json!({
        "symbol": symbol,
        "timestamp": at,
        "oi_usd": value,
        "change_1h_pct": change(1),
        "change_24h_pct": change(24),
        "history": rows.iter().map(|(at, value, _)| serde_json::json!({"timestamp": at, "oi_usd": value})).collect::<Vec<_>>()
    }))
}

pub(crate) fn parse_trading_futures_symbols(body: &serde_json::Value) -> Option<HashSet<String>> {
    let rows = body["symbols"].as_array()?;
    if rows.is_empty() {
        return None;
    }
    let active: HashSet<_> = rows
        .iter()
        .filter(|row| {
            row["status"] == "TRADING"
                && row["contractType"] == "PERPETUAL"
                && row["quoteAsset"] == "USDT"
        })
        .filter_map(|row| row["symbol"].as_str().map(str::to_string))
        .collect();
    (!active.is_empty()).then_some(active)
}

async fn futures_symbol_trading(state: &AppState, symbol: &str) -> Option<bool> {
    // One catalog lookup covers all pairs; on lookup failure, let existing endpoint checks decide.
    let mut cache = state.futures_catalog.lock().await;
    let due = cache.as_ref().is_none_or(|(at, symbols)| {
        at.elapsed() >= std::time::Duration::from_secs(if symbols.is_some() { 60 } else { 30 })
    });
    if due {
        let result = async {
            state
                .http_client
                .get("https://fapi.binance.com/fapi/v1/exchangeInfo")
                .send()
                .await?
                .error_for_status()?
                .json::<serde_json::Value>()
                .await
        }
        .await;
        let valid_response = result.is_ok();
        let symbols = match result {
            Ok(body) => parse_trading_futures_symbols(&body),
            Err(error) => {
                crate::logging::event(
                    "warn",
                    "binance_futures_catalog_failed",
                    serde_json::json!({"error":error.to_string()}),
                );
                None
            }
        };
        if symbols.is_none() && valid_response {
            crate::logging::event(
                "warn",
                "binance_futures_catalog_failed",
                serde_json::json!({"error":"invalid symbols payload"}),
            );
        }
        *cache = Some((std::time::Instant::now(), symbols));
    }
    cache
        .as_ref()
        .and_then(|(_, symbols)| symbols.as_ref().map(|symbols| symbols.contains(symbol)))
}

fn market_price_history(
    body: &serde_json::Value,
    pool_price: f64,
    now_ms: i64,
) -> Vec<serde_json::Value> {
    if !pool_price.is_finite() || pool_price <= 0.0 {
        return Vec::new();
    }
    let mut rows: Vec<(i64, f64)> = body
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|row| {
            let at = row.get(0)?.as_i64()?;
            let price = row.get(4)?.as_str()?.parse::<f64>().ok()?;
            (at > 0 && price.is_finite() && price > 0.0).then_some((at, price))
        })
        .collect();
    rows.sort_unstable_by_key(|row| row.0);
    let Some(&(at, latest)) = rows.last() else {
        return Vec::new();
    };
    if !(0..=180_000).contains(&(now_ms - at)) || !(0.8..=1.25).contains(&(latest / pool_price)) {
        return Vec::new();
    }
    rows.into_iter()
        .map(|(at, price)| serde_json::json!({"at":at,"price":price}))
        .collect()
}

fn position_ratio(body: &serde_json::Value, symbol: &str) -> Option<serde_json::Value> {
    let row = body
        .as_array()?
        .iter()
        .find(|row| row["symbol"].as_str() == Some(symbol))?;
    let ratio = row["longShortRatio"].as_str()?.parse::<f64>().ok()?;
    let at = row["timestamp"].as_i64()?;
    (ratio.is_finite()
        && ratio >= 0.0
        && (0..=7_200_000).contains(&(chrono::Utc::now().timestamp_millis() - at)))
    .then(|| serde_json::json!({"ratio":ratio,"timestamp":at,"source":"Binance 大户持仓量多空比"}))
}

fn aggregate_global_oi(body: &serde_json::Value, now: i64) -> HashMap<String, serde_json::Value> {
    let mut totals: HashMap<String, (f64, std::collections::HashSet<String>, i64)> = HashMap::new();
    for row in body.as_array().into_iter().flatten() {
        if row.get("contract_type").and_then(|x| x.as_str()) != Some("perpetual")
            || !row.get("expired_at").is_some_and(|x| x.is_null())
        {
            continue;
        }
        let Some(symbol) = row.get("index_id").and_then(|x| x.as_str()) else {
            continue;
        };
        let Some(market) = row.get("market").and_then(|x| x.as_str()) else {
            continue;
        };
        let Some(value) = row.get("open_interest").and_then(|x| x.as_f64()) else {
            continue;
        };
        let Some(at) = row.get("last_traded_at").and_then(|x| x.as_i64()) else {
            continue;
        };
        if symbol.is_empty()
            || !value.is_finite()
            || value <= 0.0
            || !(0..=7_200).contains(&(now - at))
        {
            continue;
        }
        let entry = totals
            .entry(symbol.to_ascii_uppercase())
            .or_insert_with(|| (0.0, std::collections::HashSet::new(), 0));
        entry.0 += value;
        entry.1.insert(market.to_string());
        entry.2 = entry.2.max(at);
    }
    totals.into_iter().map(|(symbol, (value, markets, at))| {
        (symbol, serde_json::json!({"oi_usd":value, "market_count":markets.len(), "timestamp":at*1000}))
    }).collect()
}

async fn global_oi_for(
    state: &AppState,
    client: &reqwest::Client,
    base: &str,
) -> Option<serde_json::Value> {
    // ponytail: one lock serializes the shared fetch; a refresh failure keeps the last snapshot for 60 seconds.
    let mut cache = state.global_oi.lock().await;
    if cache.as_ref().map_or(true, |(at, _)| {
        at.elapsed() >= std::time::Duration::from_secs(60)
    }) {
        let response = match client
            .get("https://api.coingecko.com/api/v3/derivatives")
            .send()
            .await
        {
            Ok(response) => response,
            Err(e) => {
                crate::logging::event(
                    "warn",
                    "global_oi_request_failed",
                    serde_json::json!({"provider":"coingecko","error":e.to_string(),"fallback":"previous_value","retry_seconds":60}),
                );
                let old = cache.take().map(|(_, value)| value).unwrap_or_default();
                *cache = Some((
                    std::time::Instant::now() - std::time::Duration::from_secs(240),
                    old,
                ));
                return cache.as_ref()?.1.get(base).cloned();
            }
        };
        if !response.status().is_success() {
            crate::logging::event(
                "warn",
                "global_oi_request_failed",
                serde_json::json!({"provider":"coingecko","status":response.status().as_u16(),"fallback":"previous_value","retry_seconds":60}),
            );
            let old = cache.take().map(|(_, value)| value).unwrap_or_default();
            *cache = Some((
                std::time::Instant::now() - std::time::Duration::from_secs(240),
                old,
            ));
            return cache.as_ref()?.1.get(base).cloned();
        }
        let body: serde_json::Value = match response.json().await {
            Ok(body) => body,
            Err(e) => {
                crate::logging::event(
                    "warn",
                    "global_oi_decode_failed",
                    serde_json::json!({"provider":"coingecko","error":e.to_string(),"fallback":"previous_value","retry_seconds":60}),
                );
                let old = cache.take().map(|(_, value)| value).unwrap_or_default();
                *cache = Some((
                    std::time::Instant::now() - std::time::Duration::from_secs(240),
                    old,
                ));
                return cache.as_ref()?.1.get(base).cloned();
            }
        };
        *cache = Some((
            std::time::Instant::now(),
            aggregate_global_oi(&body, chrono::Utc::now().timestamp()),
        ));
    }
    cache.as_ref()?.1.get(base).cloned()
}

#[cfg(test)]
mod oi_tests {
    #[test]
    fn expired_five_minute_signal_loses_its_tags_and_score_bonus() {
        let now = chrono::Utc::now();
        let mut row = serde_json::json!({
            "signal_at": (now - chrono::Duration::minutes(6)).to_rfc3339(),
            "signal_score": 58,
            "signal_reasons": "[\"healthy_liquidity_band\",\"m5_volume_impulse\",\"m5_buy_dominance\",\"positive_price_trend\"]",
            "signal_risk_flags": "[\"thin_m5_activity\",\"sell_pressure\"]",
            "short_momentum_score": 70,
            "prediction_reasons": "[\"m5_buy_dominance\"]"
        });
        super::expire_short_signal(&mut row, now);
        assert_eq!(row["signal_score"], 35);
        assert_eq!(row["signal_reasons"], "[\"healthy_liquidity_band\"]");
        assert_eq!(row["signal_risk_flags"], "[]");
        assert!(row["short_momentum_score"].is_null());
        assert!(row["prediction_reasons"].is_null());
        assert_eq!(row["short_signal_stale"], true);

        let mut fresh = serde_json::json!({"signal_at":now.to_rfc3339(),"signal_score":20,"signal_reasons":"[\"m5_buy_dominance\"]"});
        super::expire_short_signal(&mut fresh, now);
        assert_eq!(fresh["signal_score"], 20);
        assert_eq!(fresh["signal_reasons"], "[\"m5_buy_dominance\"]");

        let mut zero = serde_json::json!({"signal_at":(now - chrono::Duration::minutes(6)).to_rfc3339(),"signal_score":0,"signal_risk_flags":"[\"sell_pressure\"]"});
        super::expire_short_signal(&mut zero, now);
        assert_eq!(zero["signal_score"], 0);
    }
    #[test]
    fn depth_risks_require_fresh_ninety_percent_coverage() {
        let now = chrono::Utc::now();
        let rows = vec![
            serde_json::json!({"pair_address":"a","liquidity_usd":90_000.0}),
            serde_json::json!({"pair_address":"b","liquidity_usd":10_000.0}),
        ];
        let mut cache = std::collections::HashMap::new();
        let depth = |at: chrono::DateTime<chrono::Utc>| super::CacheEntry {
            checked_at: std::time::Instant::now(),
            loading: false,
            failures: 0,
            value: Some(
                serde_json::json!({"available":true,"calculated_at":at.to_rfc3339(),"bands_pct":[1],"support_usd":[1000.0],"pressure_usd":[4000.0]}),
            ),
        };
        cache.insert("a".into(), depth(now));
        let flags = super::depth_risk_flags(&rows, &cache, now);
        assert!(flags.contains(&"thin_downside_depth".into()));
        assert!(flags.contains(&"upside_depth_heavy".into()));
        cache.insert("a".into(), depth(now - chrono::Duration::minutes(6)));
        assert!(super::depth_risk_flags(&rows, &cache, now).is_empty());
        cache.insert("a".into(), depth(now));
        let mut partial = rows.clone();
        partial[0]["liquidity_usd"] = serde_json::json!(89_000.0);
        partial[1]["liquidity_usd"] = serde_json::json!(11_000.0);
        assert!(super::depth_risk_flags(&partial, &cache, now).is_empty());
    }
    #[test]
    fn overview_keeps_all_pools_of_scored_tokens_only() {
        let now = chrono::Utc::now().to_rfc3339();
        let mut rows = vec![
            serde_json::json!({"token_address":"0xAbC","pair_address":"a","signal_score":10,"signal_reasons":"[\"tag\"]","signal_risk_flags":"[\"sell_pressure\"]","observed_at":"2026-10-23T10:00:00Z","price_usd":1,"unused":"large"}),
            serde_json::json!({"token_address":"0xabc","pair_address":"b","signal_score":0,"price_usd":2,"unused":"large"}),
            serde_json::json!({"token_address":"0xother","pair_address":"c","signal_score":0,"price_usd":3}),
        ];
        rows[0]["signal_at"] = serde_json::json!(now);
        let list = super::list_overview(rows.clone());
        assert_eq!(list.len(), 1);
        assert_eq!(list[0]["token_address"], "0xabc");
        assert_eq!(list[0]["indicator_reasons"], "[\"tag\"]");
        assert_eq!(list[0]["observed_at"], "2026-10-23T10:00:00Z");
        assert!(list[0].get("price_usd").is_none());
        let display = super::display_overview(rows);
        assert_eq!(display.len(), 2);
        assert_eq!(display[1]["pair_address"], "b");
        assert_eq!(display[1]["price_usd"], 2);
        assert_eq!(display[0]["signal_risk_flags"], "[\"sell_pressure\"]");
        assert_eq!(display[0]["signal_at"], now);
        assert!(display[0].get("unused").is_none());
    }

    #[test]
    fn large_transfers_use_token_quantity_and_ignore_old_or_unrelated_events() {
        let now = chrono::Utc::now().timestamp();
        let body = serde_json::json!({"operations":[
            {"timestamp":now,"transactionHash":"0x1111111111111111111111111111111111111111111111111111111111111111","tokenInfo":{"address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","decimals":"6"},"type":"transfer","value":"1500000000000","from":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","to":"0xcccccccccccccccccccccccccccccccccccccccc"},
            {"timestamp":now,"transactionHash":"0x2222222222222222222222222222222222222222222222222222222222222222","tokenInfo":{"address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","decimals":"6"},"type":"transfer","value":"1000000000000","from":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","to":"0xcccccccccccccccccccccccccccccccccccccccc"},
            {"timestamp":now-90_000,"transactionHash":"0x3333333333333333333333333333333333333333333333333333333333333333","tokenInfo":{"address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","decimals":"6"},"type":"transfer","value":"2000000000000","from":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","to":"0xcccccccccccccccccccccccccccccccccccccccc"},
            {"timestamp":now,"transactionHash":"0x4444444444444444444444444444444444444444444444444444444444444444","tokenInfo":{"address":"0xdddddddddddddddddddddddddddddddddddddddd","decimals":"6"},"type":"transfer","value":"2000000000000","from":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","to":"0xcccccccccccccccccccccccccccccccccccccccc"}
        ]});
        let transfers = super::large_transfers(&body, "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        assert_eq!(transfers.len(), 1);
        assert_eq!(transfers[0]["amount"], 1_500_000.0);
        assert_eq!(
            transfers[0]["from"],
            "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        );
        assert_eq!(
            transfers[0]["to"],
            "0xcccccccccccccccccccccccccccccccccccccccc"
        );
    }
    #[test]
    fn whale_trades_only_count_real_large_token_swaps() {
        let now = chrono::Utc::now();
        let recent = now.to_rfc3339();
        let old = (now - chrono::Duration::hours(25)).to_rfc3339();
        let body = serde_json::json!({"data":[
            {"attributes":{"block_timestamp":recent,"tx_hash":"0x1111111111111111111111111111111111111111111111111111111111111111","tx_from_address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"sell","from_token_address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","to_token_address":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","volume_in_usd":"12000","is_anomalous":false}},
            {"attributes":{"block_timestamp":recent,"tx_hash":"0x2222222222222222222222222222222222222222222222222222222222222222","tx_from_address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"buy","from_token_address":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","to_token_address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","volume_in_usd":"15000","is_anomalous":false}},
            {"attributes":{"block_timestamp":recent,"tx_hash":"0x3333333333333333333333333333333333333333333333333333333333333333","tx_from_address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"sell","from_token_address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","to_token_address":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","volume_in_usd":"9999","is_anomalous":false}},
            {"attributes":{"block_timestamp":recent,"tx_hash":"0x4444444444444444444444444444444444444444444444444444444444444444","tx_from_address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"sell","from_token_address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","to_token_address":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","volume_in_usd":"50000","is_anomalous":true}},
            {"attributes":{"block_timestamp":old,"tx_hash":"0x5555555555555555555555555555555555555555555555555555555555555555","tx_from_address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"sell","from_token_address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","to_token_address":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","volume_in_usd":"50000","is_anomalous":false}},
            {"attributes":{"block_timestamp":recent,"tx_hash":"0x6666666666666666666666666666666666666666666666666666666666666666","tx_from_address":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"sell","from_token_address":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","to_token_address":"0xcccccccccccccccccccccccccccccccccccccccc","volume_in_usd":"50000","is_anomalous":false}}
        ]});
        let trades = super::whale_trades(&body, "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        assert_eq!(trades.len(), 2);
        assert_eq!(trades[0]["side"], "sell");
        assert_eq!(trades[0]["usd"], 12000.0);
        assert_eq!(trades[1]["side"], "buy");
    }
    #[tokio::test]
    async fn shared_cache_starts_once_and_serves_stale_data() {
        let cache = std::sync::Arc::new(tokio::sync::Mutex::new(std::collections::HashMap::new()));
        let ttl = std::time::Duration::from_secs(300);
        let (first, start) = super::cached_or_pending(&cache, "pair", ttl).await;
        assert_eq!(first["pending"], true);
        assert!(start);
        assert!(!super::cached_or_pending(&cache, "pair", ttl).await.1);
        assert!(!super::cache_due(&cache, "pair", ttl).await);
        super::store_result(&cache, "pair".into(), serde_json::json!({"available":true})).await;
        assert!(
            !super::cache_due(&cache, "pair", ttl).await,
            "page refresh must satisfy the scheduled warmup"
        );
        cache.lock().await.insert(
            "pair".into(),
            super::CacheEntry {
                checked_at: std::time::Instant::now() - ttl,
                value: Some(serde_json::json!({"available":true})),
                loading: false,
                failures: 0,
            },
        );
        assert!(super::cache_due(&cache, "pair", ttl).await);
        let (stale, start) = super::cached_or_pending(&cache, "pair", ttl).await;
        assert_eq!(stale["available"], true);
        assert!(start);
        assert!(!super::cached_or_pending(&cache, "pair", ttl).await.1);
    }
    #[tokio::test]
    async fn prewarm_waits_for_current_pool_to_finish() {
        let cache = std::sync::Arc::new(tokio::sync::Mutex::new(std::collections::HashMap::new()));
        cache.lock().await.insert(
            "pair".into(),
            super::CacheEntry {
                checked_at: std::time::Instant::now(),
                value: None,
                loading: true,
                failures: 0,
            },
        );
        let waiting_cache = cache.clone();
        let waiter =
            tokio::spawn(async move { super::wait_for_result(&waiting_cache, "pair").await });
        tokio::task::yield_now().await;
        assert!(!waiter.is_finished());
        cache.lock().await.get_mut("pair").unwrap().loading = false;
        tokio::time::timeout(std::time::Duration::from_secs(1), waiter)
            .await
            .unwrap()
            .unwrap();
    }

    #[tokio::test]
    async fn failed_refresh_keeps_last_value_and_backs_off() {
        let cache = std::sync::Arc::new(tokio::sync::Mutex::new(std::collections::HashMap::new()));
        let calculated_at = chrono::Utc::now().to_rfc3339();
        super::store_result(
            &cache,
            "pair".into(),
            serde_json::json!({"available":true,"calculated_at":calculated_at}),
        )
        .await;
        super::store_result(
            &cache,
            "pair".into(),
            serde_json::json!({"available":false,"reason":"timeout"}),
        )
        .await;
        let (value, start) =
            super::cached_or_pending(&cache, "pair", std::time::Duration::ZERO).await;
        assert_eq!(value["available"], true);
        assert_eq!(value["calculated_at"], calculated_at);
        assert_eq!(value["stale"], true);
        assert_eq!(value["last_error"], "timeout");
        assert!(!start);
        assert!(super::cache_due(&cache, "other_pair", std::time::Duration::from_secs(300)).await);
        assert_eq!(cache.lock().await.get("pair").unwrap().failures, 1);
        super::store_result(&cache, "pair".into(), serde_json::json!({"available":true,"calculated_at":(chrono::Utc::now()-chrono::Duration::hours(2)).to_rfc3339()})).await;
        super::store_result(
            &cache,
            "pair".into(),
            serde_json::json!({"available":false,"reason":"still down"}),
        )
        .await;
        assert_eq!(
            cache
                .lock()
                .await
                .get("pair")
                .unwrap()
                .value
                .as_ref()
                .unwrap()["available"],
            false
        );
    }

    #[test]
    fn depth_warm_candidates_prioritize_ninety_five_percent_liquidity() {
        let rows = vec![
            serde_json::json!({"token_address":"a","pair_address":"pancake","dex_id":"pancakeswap","signal_score":20,"liquidity_usd":600.0,"price_usd":1.0}),
            serde_json::json!({"token_address":"a","pair_address":"uniswap","dex_id":"uniswap","signal_score":20,"liquidity_usd":360.0,"price_usd":1.0}),
            serde_json::json!({"token_address":"a","pair_address":"small","signal_score":20,"liquidity_usd":40.0,"price_usd":1.0}),
            serde_json::json!({"token_address":"b","pair_address":"b1","signal_score":10,"liquidity_usd":100.0,"price_usd":1.0}),
            serde_json::json!({"token_address":"c","pair_address":"no_price","signal_score":9,"liquidity_usd":100.0,"price_usd":0.0}),
            serde_json::json!({"token_address":"c","pair_address":"no_liquidity","signal_score":9,"liquidity_usd":0.0,"price_usd":1.0}),
            serde_json::json!({"token_address":"a","pair_address":"dust","signal_score":20,"liquidity_usd":4.0,"price_usd":1.0}),
        ];
        let (high, regular, cold) = super::depth_warm_candidates(&rows);
        assert!(high.is_empty());
        assert_eq!(regular, vec!["pancake", "uniswap", "b1"]);
        assert_eq!(cold, vec!["small"]);
        assert!(!high
            .iter()
            .chain(&regular)
            .chain(&cold)
            .any(|pair| pair == "dust"));
    }

    #[test]
    fn depth_warm_candidates_follow_visible_top_fifty() {
        let rows: Vec<_> = (0..51)
            .map(|i| serde_json::json!({"token_address":format!("token{i}"),"pair_address":format!("pair{i}"),"signal_score":100-i,"liquidity_usd":100.0,"price_usd":1.0}))
            .collect();
        let (high, regular, cold) = super::depth_warm_candidates(&rows);
        assert_eq!(high.len(), 40);
        assert_eq!(regular.len(), 10);
        assert!(!high.iter().chain(&regular).any(|pair| pair == "pair50"));
        assert!(cold.is_empty());
    }

    #[tokio::test]
    async fn depth_rotation_skips_fresh_pools_without_wasting_refresh_slots() {
        let cache: super::ResultCache =
            std::sync::Arc::new(tokio::sync::Mutex::new(std::collections::HashMap::new()));
        let now = std::time::Instant::now();
        for (pair, age) in [("fresh", 0), ("due1", 600), ("due2", 600), ("due3", 600)] {
            cache.lock().await.insert(
                pair.to_string(),
                super::CacheEntry {
                    checked_at: now - std::time::Duration::from_secs(age),
                    value: Some(serde_json::json!({"available":true})),
                    loading: false,
                    failures: 0,
                },
            );
        }
        let pairs = ["fresh", "due1", "due2", "due3"].map(str::to_string);
        let mut next = 0;
        let (due, scanned) = super::rotating_due(
            &cache,
            &pairs,
            &mut next,
            2,
            std::time::Duration::from_secs(240),
        )
        .await;
        assert_eq!(due, ["due1", "due2"]);
        assert_eq!(scanned, 3);
        assert_eq!(next, 3);
    }

    #[tokio::test]
    async fn depth_warm_jobs_keep_only_three_pools_in_flight() {
        let gate = std::sync::Arc::new(tokio::sync::Semaphore::new(0));
        let (started, mut receiver) = tokio::sync::mpsc::unbounded_channel();
        let worker_gate = gate.clone();
        let runner = tokio::spawn(super::run_depth_jobs(
            vec!["a".into(), "b".into(), "c".into(), "d".into()],
            move |pair| {
                let gate = worker_gate.clone();
                let started = started.clone();
                async move {
                    started.send(pair).unwrap();
                    gate.acquire().await.unwrap().forget();
                }
            },
        ));
        assert!(
            tokio::time::timeout(std::time::Duration::from_secs(1), receiver.recv())
                .await
                .unwrap()
                .is_some()
        );
        assert!(
            tokio::time::timeout(std::time::Duration::from_secs(1), receiver.recv())
                .await
                .unwrap()
                .is_some()
        );
        assert!(
            tokio::time::timeout(std::time::Duration::from_secs(1), receiver.recv())
                .await
                .unwrap()
                .is_some()
        );
        assert!(
            receiver.try_recv().is_err(),
            "fourth pool started before a slot freed"
        );
        gate.add_permits(4);
        tokio::time::timeout(std::time::Duration::from_secs(1), runner)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(receiver.try_recv().unwrap(), "d");
    }

    #[test]
    fn whale_warm_batch_prioritizes_display_scores_above_sixty_and_rotates_others() {
        let rows: Vec<_> = (0..20)
            .map(|i| serde_json::json!({"token_address":format!("token{i}"),"pair_address":format!("pair{i}"),"signal_score":if i<6 {70-i} else {66-i},"liquidity_usd":100.0,"price_usd":1.0}))
            .collect();
        let (first, hot, high) = super::whale_warm_batch(&rows, 0, 0);
        assert_eq!((hot, high), (6, 14));
        assert_eq!(
            first[..10],
            (0..10).map(|i| format!("pair{i}")).collect::<Vec<_>>()
        );
        let (second, _, _) = super::whale_warm_batch(&rows, 0, 4);
        assert_eq!(second[..20], first[..20]);

        let mut rows = rows;
        for i in 0..8 {
            rows.push(serde_json::json!({"token_address":format!("token{i}"),"pair_address":format!("secondary{i}"),"signal_score":0,"liquidity_usd":100.0,"price_usd":1.0}));
            rows.push(serde_json::json!({"token_address":format!("token{i}"),"pair_address":format!("tiny{i}"),"signal_score":0,"liquidity_usd":1.0,"price_usd":1.0}));
        }
        let (first, hot, high) = super::whale_warm_batch(&rows, 0, 0);
        let (second, _, _) = super::whale_warm_batch(&rows, 6, 1);
        assert_eq!(
            &first[..hot],
            &["pair0", "pair1", "pair2", "pair3", "pair4", "pair5"]
        );
        assert_eq!(
            first[..hot + high],
            (0..20).map(|i| format!("pair{i}")).collect::<Vec<_>>()
        );
        assert_eq!(first[hot + high], "secondary0");
        assert_eq!(second[hot + high], "secondary1");
        assert!(!first
            .iter()
            .chain(&second)
            .any(|pair| pair.starts_with("tiny")));
    }

    #[test]
    fn whale_warm_batch_only_prefetches_visible_top_fifty() {
        let rows: Vec<_> = (0..51)
            .map(|i| serde_json::json!({"token_address":format!("token{i}"),"pair_address":format!("pair{i}"),"signal_score":100-i,"liquidity_usd":100.0,"price_usd":1.0}))
            .collect();
        let (pairs, _, _) = super::whale_warm_batch(&rows, 0, 0);
        assert_eq!(pairs.len(), 50);
        assert!(!pairs.contains(&"pair50".to_string()));
    }

    #[test]
    fn whale_warm_batch_keeps_visible_token_after_short_signal_expires() {
        let rows = vec![serde_json::json!({
            "token_address":"token", "pair_address":"main", "signal_score":65,
            "short_momentum_score":0, "signal_reasons":"[]",
            "signal_at":(chrono::Utc::now()-chrono::Duration::minutes(6)).to_rfc3339(),
            "liquidity_usd":100.0, "price_usd":1.0
        })];
        let (pairs, hot, _) = super::whale_warm_batch(&rows, 0, 0);
        assert_eq!(pairs, ["main"]);
        assert_eq!(
            hot, 1,
            "the displayed token must use the main-pool priority slot"
        );
    }

    #[test]
    fn whale_priority_refreshes_oldest_success_first() {
        let mut pairs = vec![
            "fresh".to_string(),
            "old".to_string(),
            "missing".to_string(),
        ];
        let now = std::time::Instant::now();
        let entries = std::collections::HashMap::from([
            (
                "fresh".to_string(),
                super::CacheEntry {
                    checked_at: now,
                    value: Some(
                        serde_json::json!({"available":true,"calculated_at":"2026-10-23T13:00:00+00:00"}),
                    ),
                    loading: false,
                    failures: 0,
                },
            ),
            (
                "old".to_string(),
                super::CacheEntry {
                    checked_at: now,
                    value: Some(
                        serde_json::json!({"available":true,"calculated_at":"2026-10-23T12:00:00+00:00"}),
                    ),
                    loading: false,
                    failures: 0,
                },
            ),
        ]);
        super::sort_whale_priority(&mut pairs, &entries);
        assert_eq!(pairs, ["missing", "old", "fresh"]);
    }

    #[test]
    fn gecko_pacer_adjusts_between_five_and_twenty_seconds() {
        let mut pacer = super::GeckoPacer::new();
        assert_eq!(pacer.spacing_secs, 20);
        for _ in 0..75 {
            pacer.after_response(Some(axum::http::StatusCode::OK), None, 0);
        }
        assert_eq!(pacer.spacing_secs, 5);
        pacer.after_response(Some(axum::http::StatusCode::OK), None, 1);
        assert_eq!(pacer.spacing_secs, 5);
        pacer.after_response(Some(axum::http::StatusCode::OK), None, 1);
        assert_eq!(pacer.spacing_secs, 7);
        for _ in 0..50 {
            pacer.after_response(Some(axum::http::StatusCode::OK), None, 1);
        }
        assert_eq!(pacer.spacing_secs, 20);
        for _ in 0..5 {
            pacer.after_response(Some(axum::http::StatusCode::OK), None, 0);
        }
        assert_eq!(pacer.spacing_secs, 19);
        pacer
            .recent_limits
            .push_front(std::time::Instant::now() - std::time::Duration::from_secs(301));
        pacer.after_response(Some(axum::http::StatusCode::OK), None, 0);
        assert!(pacer
            .recent_limits
            .iter()
            .all(|time| time.elapsed().as_secs() < 300));
        assert_eq!(
            pacer.after_response(
                Some(axum::http::StatusCode::TOO_MANY_REQUESTS),
                Some(120),
                0
            ),
            Some(120)
        );
        assert_eq!(pacer.spacing_secs, 20);
        assert_eq!(
            pacer.after_response(
                Some(axum::http::StatusCode::TOO_MANY_REQUESTS),
                Some(u64::MAX),
                0,
            ),
            Some(3600)
        );
    }

    #[test]
    fn status_separates_missing_contract_from_failure_and_uses_data_time() {
        let now = chrono::Utc::now();
        let missing =
            serde_json::json!({"available":false,"reason":"无正在交易的 Binance USDT 永续合约"});
        let failed = serde_json::json!({"available":false,"reason":"Binance OI HTTP 403"});
        let old = serde_json::json!({"available":true,"calculated_at":(now-chrono::Duration::seconds(360)).to_rfc3339()});
        assert!(super::oi_unsupported(&missing));
        assert!(!super::oi_unsupported(&failed));
        assert!(super::result_age_seconds(&old, now).unwrap() >= 360);
    }

    #[test]
    fn whale_warmup_does_not_start_during_provider_cooldown() {
        let mut pacer = super::GeckoPacer::new();
        pacer.after_response(Some(axum::http::StatusCode::TOO_MANY_REQUESTS), None, 0);
        assert!(pacer.cooling_down(std::time::Instant::now()));
        pacer.next = std::time::Instant::now() + std::time::Duration::from_secs(pacer.spacing_secs);
        assert!(!pacer.cooling_down(std::time::Instant::now()));
    }

    #[test]
    fn futures_catalog_keeps_only_trading_usdt_perpetuals() {
        let catalog = serde_json::json!({"symbols":[
            {"symbol":"BTCUSDT","status":"TRADING","contractType":"PERPETUAL","quoteAsset":"USDT"},
            {"symbol":"TOKENUSDT","status":"SETTLING","contractType":"PERPETUAL","quoteAsset":"USDT"},
            {"symbol":"ETHUSDT","status":"TRADING","contractType":"CURRENT_QUARTER","quoteAsset":"USDT"},
            {"symbol":"BTCUSDC","status":"TRADING","contractType":"PERPETUAL","quoteAsset":"USDC"}
        ]});
        let symbols = super::parse_trading_futures_symbols(&catalog).unwrap();
        assert_eq!(
            symbols,
            std::collections::HashSet::from(["BTCUSDT".to_string()])
        );
        assert!(
            super::parse_trading_futures_symbols(&serde_json::json!({"error":"unavailable"}))
                .is_none()
        );
    }

    #[tokio::test]
    async fn invalid_futures_symbol_is_distinct_from_an_empty_price_history() {
        use tokio::io::AsyncWriteExt;
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            stream
                .write_all(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                .await
                .unwrap();
        });
        let result =
            super::fetch_binance_price_history(&reqwest::Client::new(), &url, "MISSINGUSDT", 1.0)
                .await;
        server.await.unwrap();
        assert!(format!("{result:?}").contains("InvalidSymbol"));
    }

    #[tokio::test]
    async fn gecko_proxy_connection_failure_falls_back_to_direct() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let closed = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let closed_port = closed.local_addr().unwrap().port();
        drop(closed);
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}/trades", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut buffer = [0; 512];
            stream.read(&mut buffer).await.unwrap();
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                .await
                .unwrap();
        });
        let proxy = super::GeckoProxy {
            client: reqwest::Client::builder()
                .no_proxy()
                .proxy(reqwest::Proxy::all(format!("http://127.0.0.1:{closed_port}")).unwrap())
                .build()
                .unwrap(),
            retry_at: tokio::sync::Mutex::new(std::time::Instant::now()),
        };
        let (response, used_proxy, rate_limits) = super::gecko_request_with_fallback(
            &reqwest::Client::builder().no_proxy().build().unwrap(),
            &[proxy],
            &std::sync::atomic::AtomicUsize::new(0),
            &url,
        )
        .await;
        assert!(!used_proxy);
        assert_eq!(rate_limits, 0);
        assert_eq!(response.unwrap().status(), reqwest::StatusCode::OK);
        server.await.unwrap();
    }

    #[tokio::test]
    async fn gecko_proxy_rate_limit_is_counted_after_direct_fallback() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let proxy_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let direct_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let proxy_url = format!("http://{}", proxy_listener.local_addr().unwrap());
        let url = format!("http://{}/trades", direct_listener.local_addr().unwrap());
        let proxy_server = tokio::spawn(async move {
            let (mut stream, _) = proxy_listener.accept().await.unwrap();
            stream.read(&mut [0; 512]).await.unwrap();
            stream
                .write_all(b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n")
                .await
                .unwrap();
        });
        let direct_server = tokio::spawn(async move {
            let (mut stream, _) = direct_listener.accept().await.unwrap();
            stream.read(&mut [0; 512]).await.unwrap();
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                .await
                .unwrap();
        });
        let proxy = super::GeckoProxy {
            client: reqwest::Client::builder()
                .no_proxy()
                .proxy(reqwest::Proxy::all(proxy_url).unwrap())
                .build()
                .unwrap(),
            retry_at: tokio::sync::Mutex::new(std::time::Instant::now()),
        };
        let (response, used_proxy, rate_limits) = super::gecko_request_with_fallback(
            &reqwest::Client::builder().no_proxy().build().unwrap(),
            &[proxy],
            &std::sync::atomic::AtomicUsize::new(0),
            &url,
        )
        .await;
        assert!(!used_proxy);
        assert_eq!(rate_limits, 1);
        assert_eq!(response.unwrap().status(), reqwest::StatusCode::OK);
        proxy_server.await.unwrap();
        direct_server.await.unwrap();
    }

    #[test]
    fn priority_freshness_uses_data_time_and_counts_missing_results() {
        let now = chrono::Utc::now();
        let mut cache = std::collections::HashMap::new();
        for (pair, age) in [("fresh", 60), ("old", 360)] {
            cache.insert(
                pair.to_string(),
                super::CacheEntry {
                    checked_at: std::time::Instant::now(),
                    value: Some(serde_json::json!({"available":true,"calculated_at":(now-chrono::Duration::seconds(age)).to_rfc3339()})),
                    loading: false,
                    failures: 0,
                },
            );
        }
        let pairs = vec![
            "fresh".to_string(),
            "old".to_string(),
            "missing".to_string(),
        ];
        assert_eq!(super::priority_freshness(&cache, &pairs, now), (1, 1));
    }

    #[test]
    fn priority_whales_are_due_before_the_five_minute_display_deadline() {
        let entry = super::CacheEntry {
            checked_at: std::time::Instant::now() - std::time::Duration::from_secs(150),
            value: Some(serde_json::json!({"available":true})),
            loading: false,
            failures: 0,
        };
        assert!(super::cache_entry_due(&entry, super::whale_ttl(true)));
        assert!(!super::cache_entry_due(&entry, super::whale_ttl(false)));
    }

    #[test]
    fn whale_batch_reserves_a_secondary_slot_only_when_priority_fits() {
        assert_eq!(super::whale_high_limit(5, true, 5), 5);
        assert_eq!(super::whale_high_limit(5, true, 4), 4);
        assert_eq!(super::whale_high_limit(5, true, 0), 4);
        assert_eq!(super::whale_high_limit(5, false, 0), 5);
    }

    #[test]
    fn oi_warm_batch_reaches_tokens_after_the_first_fifty() {
        let rows: Vec<_> = (0..120)
            .map(|i| {
                serde_json::json!({
                    "token_address":format!("token{i}"),
                    "pair_address":format!("pair{i}"),
                    "signal_score":120-i,
                })
            })
            .collect();
        let mut next = 0;
        let first = super::oi_warm_batch(&rows, &mut next);
        assert_eq!(first.len(), 100);
        assert_eq!(first[0], "pair0");
        assert_eq!(first[99], "pair99");
        let second = super::oi_warm_batch(&rows, &mut next);
        assert_eq!(second[50], "pair100");
        assert_eq!(second[69], "pair119");
        assert_eq!(second[70], "pair50");
    }

    #[test]
    fn oi_priority_uses_score_but_fetches_the_displayed_liquid_pool() {
        let rows = vec![
            serde_json::json!({"token_address":"a","pair_address":"a1","signal_score":1,"liquidity_usd":100}),
            serde_json::json!({"token_address":"b","pair_address":"b1","signal_score":5,"liquidity_usd":20}),
            serde_json::json!({"token_address":"a","pair_address":"a2","signal_score":9,"liquidity_usd":10}),
        ];
        assert_eq!(
            super::ranked_oi_pairs(&rows),
            vec![("a".into(), "a1".into()), ("b".into(), "b1".into())]
        );
    }
    #[test]
    fn position_ratio_requires_matching_fresh_symbol() {
        let now = chrono::Utc::now().timestamp_millis();
        let body =
            serde_json::json!([{"symbol":"TESTUSDT","longShortRatio":"1.25","timestamp":now}]);
        assert_eq!(
            super::position_ratio(&body, "TESTUSDT").unwrap()["ratio"],
            1.25
        );
        assert!(super::position_ratio(&body, "OTHERUSDT").is_none());
    }
    #[test]
    fn computes_oi_changes_from_exact_hourly_samples() {
        let mut rows = Vec::new();
        for hour in 0..=24 {
            rows.push(serde_json::json!({
                "symbol": "TESTUSDT",
                "sumOpenInterestValue": (100 + hour).to_string(),
                "sumOpenInterest": (100 + hour).to_string(),
                "timestamp": 1_000_000 + hour * 3_600_000,
            }));
        }
        let result = super::oi_from_history(&serde_json::json!(rows), "TESTUSDT").unwrap();
        assert_eq!(result["oi_usd"], 124.0);
        assert!((result["change_1h_pct"].as_f64().unwrap() - 100.0 / 123.0).abs() < 1e-9);
        assert_eq!(result["change_24h_pct"], 24.0);
    }

    #[test]
    fn missing_hourly_sample_does_not_invent_change() {
        let rows = serde_json::json!([
            {"symbol":"TESTUSDT","sumOpenInterestValue":"100","sumOpenInterest":"100","timestamp":1_000_000},
            {"symbol":"TESTUSDT","sumOpenInterestValue":"120","sumOpenInterest":"120","timestamp":8_200_000}
        ]);
        let result = super::oi_from_history(&rows, "TESTUSDT").unwrap();
        assert!(result["change_1h_pct"].is_null());
        assert!(result["change_24h_pct"].is_null());
    }

    #[test]
    fn oi_change_uses_position_quantity_not_dollar_value() {
        let rows = serde_json::json!([
            {"symbol":"TESTUSDT","sumOpenInterestValue":"100","sumOpenInterest":"100","timestamp":1_000_000},
            {"symbol":"TESTUSDT","sumOpenInterestValue":"110","sumOpenInterest":"90","timestamp":4_600_000}
        ]);
        let result = super::oi_from_history(&rows, "TESTUSDT").unwrap();
        assert_eq!(result["oi_usd"], 110.0);
        assert!((result["change_1h_pct"].as_f64().unwrap() + 10.0).abs() < 1e-9);
    }

    #[test]
    fn global_oi_sums_fresh_perpetuals_across_markets() {
        let now = chrono::Utc::now().timestamp();
        let body = serde_json::json!([
            {"index_id":"TEST","market":"A","contract_type":"perpetual","expired_at":null,"open_interest":100.0,"last_traded_at":now},
            {"index_id":"TEST","market":"B","contract_type":"perpetual","expired_at":null,"open_interest":200.0,"last_traded_at":now},
            {"index_id":"TEST","market":"B","contract_type":"future","expired_at":null,"open_interest":1000.0,"last_traded_at":now},
            {"index_id":"TEST","market":"C","contract_type":"perpetual","expired_at":null,"open_interest":1000.0,"last_traded_at":now-10_000},
            {"index_id":"OTHER","market":"D","contract_type":"perpetual","expired_at":null,"open_interest":1000.0,"last_traded_at":now}
        ]);
        let result = super::aggregate_global_oi(&body, now);
        assert_eq!(result["TEST"]["oi_usd"], 300.0);
        assert_eq!(result["TEST"]["market_count"], 2);
    }

    #[test]
    fn market_price_history_requires_fresh_matching_price() {
        let now = 1_800_000_000_000_i64;
        let rows = serde_json::json!([
            [now - 120_000, "99", "101", "98", "99.5"],
            [now - 60_000, "99.5", "101", "99", "100.5"]
        ]);
        assert_eq!(super::market_price_history(&rows, 100.0, now).len(), 2);
        assert!(super::market_price_history(&rows, 10.0, now).is_empty());
        assert!(super::market_price_history(&rows, 100.0, now + 300_000).is_empty());
    }
}

async fn overview(
    State(state): State<AppState>,
    Query(params): Query<OverviewParams>,
) -> Result<Response, (StatusCode, Json<serde_json::Value>)> {
    let limit = params.limit.unwrap_or(200).min(5000);
    let db_path = state.db_path.as_ref().clone();
    let result = tokio::task::spawn_blocking(move || crate::db::overview(&db_path, limit))
        .await
        .map_err(axum_error)?;
    match result {
        Ok(rows) => {
            let mut response =
                Json(serde_json::Value::Array(display_overview(rows))).into_response();
            response.headers_mut().insert(
                header::CACHE_CONTROL,
                HeaderValue::from_static("private, max-age=30"),
            );
            Ok(response)
        }
        Err(e) => Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": e.to_string()})),
        )),
    }
}

async fn tokens(
    State(state): State<AppState>,
) -> Result<Response, (StatusCode, Json<serde_json::Value>)> {
    let path = state.db_path.as_ref().clone();
    let rows = tokio::task::spawn_blocking(move || crate::db::overview(&path, 5000))
        .await
        .map_err(axum_error)?
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error":e.to_string()})),
            )
        })?;
    let mut response = Json(serde_json::json!(list_overview(rows))).into_response();
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        HeaderValue::from_static("private, max-age=30"),
    );
    Ok(response)
}

async fn structure(
    State(state): State<AppState>,
    Query(params): Query<StructureParams>,
) -> Result<Response, (StatusCode, Json<serde_json::Value>)> {
    let limit = params.limit.unwrap_or(50).clamp(1, 200);
    let token = params
        .token
        .map(|token| token.to_ascii_lowercase())
        .filter(|token| !token.is_empty());
    if token.as_ref().is_some_and(|token| !valid_hex(token, 42)) {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error":"无效代币地址"})),
        ));
    }
    let path = state.db_path.as_ref().clone();
    let selected_token = token.clone();
    let token_rows =
        tokio::task::spawn_blocking(move || -> std::result::Result<Vec<String>, String> {
            if let Some(token) = selected_token {
                return Ok(vec![token]);
            }
            let rows = crate::db::overview(&path, 5000).map_err(|e| e.to_string())?;
            Ok(list_overview(rows)
                .into_iter()
                .filter_map(|row| row["token_address"].as_str().map(|token| token.to_string()))
                .take(limit)
                .collect::<Vec<_>>())
        })
        .await
        .map_err(axum_error)?
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error":e.to_string()})),
            )
        })?;
    let structure_path = state.db_path.as_ref().clone();
    let items = tokio::task::spawn_blocking(move || {
        crate::engine::factor_v1::structures(&structure_path, &token_rows, token_rows.len())
    })
    .await
    .map_err(axum_error)?
    .map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error":e.to_string()})),
        )
    })?;
    let mut response = Json(serde_json::json!({"items":items})).into_response();
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        HeaderValue::from_static("private, max-age=15"),
    );
    Ok(response)
}

async fn early_warning(
    State(state): State<AppState>,
) -> Result<Response, (StatusCode, Json<serde_json::Value>)> {
    let path = state.db_path.as_ref().clone();
    let report =
        tokio::task::spawn_blocking(move || crate::engine::early_warning_replay::current(&path))
            .await
            .map_err(axum_error)?
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(serde_json::json!({"error": e.to_string()})),
                )
            })?;
    let mut response = Json(report).into_response();
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        HeaderValue::from_static("private, max-age=15"),
    );
    Ok(response)
}

async fn token_summary(
    State(state): State<AppState>,
    Path(token): Path<String>,
) -> Result<Response, (StatusCode, Json<serde_json::Value>)> {
    if !valid_hex(&token, 42) {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error":"无效代币地址"})),
        ));
    }
    let path = state.db_path.as_ref().clone();
    let rows = tokio::task::spawn_blocking(move || crate::db::overview(&path, 5000))
        .await
        .map_err(axum_error)?
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error":e.to_string()})),
            )
        })?;
    let mut pools: Vec<_> = display_overview(rows)
        .into_iter()
        .filter(|row| {
            row["token_address"]
                .as_str()
                .is_some_and(|address| address.eq_ignore_ascii_case(&token))
        })
        .collect();
    if pools.is_empty() {
        return Err((
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error":"代币不存在或未入榜"})),
        ));
    }
    let cache = state.depth_cache.lock().await;
    let depth_flags = depth_risk_flags(&pools, &cache, chrono::Utc::now());
    drop(cache);
    if !depth_flags.is_empty() {
        for pool in &mut pools {
            let mut flags: Vec<String> = pool["signal_risk_flags"]
                .as_str()
                .and_then(|raw| serde_json::from_str(raw).ok())
                .unwrap_or_default();
            flags.extend(depth_flags.iter().cloned());
            pool["signal_risk_flags"] =
                serde_json::json!(serde_json::to_string(&flags).unwrap_or_default());
        }
    }
    let mut response = Json(serde_json::json!(pools)).into_response();
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        HeaderValue::from_static("private, max-age=15"),
    );
    Ok(response)
}

fn depth_risk_flags(
    rows: &[serde_json::Value],
    cache: &HashMap<String, CacheEntry>,
    now: chrono::DateTime<chrono::Utc>,
) -> Vec<String> {
    let total_liquidity: f64 = rows
        .iter()
        .map(|row| row["liquidity_usd"].as_f64().unwrap_or(0.0).max(0.0))
        .sum();
    if total_liquidity <= 0.0 {
        return Vec::new();
    }
    let mut covered = 0.0;
    let (mut support, mut pressure) = (0.0, 0.0);
    for row in rows {
        let Some(pair) = row["pair_address"].as_str() else {
            continue;
        };
        let Some(value) = cache
            .get(&pair.to_ascii_lowercase())
            .and_then(|entry| entry.value.as_ref())
        else {
            continue;
        };
        let fresh = value["calculated_at"]
            .as_str()
            .and_then(|at| chrono::DateTime::parse_from_rfc3339(at).ok())
            .is_some_and(|at| {
                (0..=300).contains(&(now - at.with_timezone(&chrono::Utc)).num_seconds())
            });
        if !fresh
            || value["available"] != true
            || value["stale"] == true
            || value["bands_pct"][0] != 1
        {
            continue;
        }
        let Some(down) = value["support_usd"][0]
            .as_f64()
            .filter(|x| x.is_finite() && *x >= 0.0)
        else {
            continue;
        };
        let Some(up) = value["pressure_usd"][0]
            .as_f64()
            .filter(|x| x.is_finite() && *x >= 0.0)
        else {
            continue;
        };
        covered += row["liquidity_usd"].as_f64().unwrap_or(0.0).max(0.0);
        support += down;
        pressure += up;
    }
    if covered < total_liquidity * 0.9 {
        return Vec::new();
    }
    let mut flags = Vec::new();
    if support < 10_000.0 {
        flags.push("thin_downside_depth".into());
    }
    if pressure >= support * 3.0 && pressure > 0.0 {
        flags.push("upside_depth_heavy".into());
    }
    flags
}

fn expire_short_signal(row: &mut serde_json::Value, now: chrono::DateTime<chrono::Utc>) {
    let expired = row["signal_at"]
        .as_str()
        .and_then(|at| chrono::DateTime::parse_from_rfc3339(at).ok())
        .is_some_and(|at| (now - at.with_timezone(&chrono::Utc)).num_seconds() > 300);
    if !expired {
        return;
    }
    let mut score_delta = 0;
    let mut short_signal_stale = false;
    if let Some(raw) = row["signal_reasons"].as_str() {
        if let Ok(mut reasons) = serde_json::from_str::<Vec<String>>(raw) {
            reasons.retain(|reason| {
                let bonus = match reason.as_str() {
                    "m5_volume_impulse" => 6,
                    "m5_buy_dominance" => 20,
                    "positive_price_trend" => 5,
                    _ => 0,
                };
                score_delta -= bonus;
                short_signal_stale |= bonus != 0;
                bonus == 0
            });
            row["signal_reasons"] =
                serde_json::json!(serde_json::to_string(&reasons).unwrap_or_default());
        }
    }
    if let Some(raw) = row["signal_risk_flags"].as_str() {
        if let Ok(mut flags) = serde_json::from_str::<Vec<String>>(raw) {
            let sell_pressure = flags.iter().any(|flag| flag == "sell_pressure");
            short_signal_stale |=
                sell_pressure || flags.iter().any(|flag| flag == "thin_m5_activity");
            flags.retain(|flag| flag != "thin_m5_activity" && flag != "sell_pressure");
            if sell_pressure {
                score_delta += 8;
            }
            row["signal_risk_flags"] =
                serde_json::json!(serde_json::to_string(&flags).unwrap_or_default());
        }
    }
    if let Some(score) = row["signal_score"].as_i64() {
        row["signal_score"] = serde_json::json!((score + score_delta).min(score).clamp(0, 100));
    }
    if !row["short_momentum_score"].is_null() {
        short_signal_stale = true;
        row["short_momentum_score"] = serde_json::Value::Null;
        row["prediction_reasons"] = serde_json::Value::Null;
    }
    if short_signal_stale {
        row["short_signal_stale"] = serde_json::json!(true);
    }
}

pub(crate) fn list_overview(rows: Vec<serde_json::Value>) -> Vec<serde_json::Value> {
    let mut best: HashMap<String, (f64, f64, serde_json::Value)> = HashMap::new();
    let now = chrono::Utc::now();
    for mut row in rows {
        expire_short_signal(&mut row, now);
        let score = row["short_momentum_score"]
            .as_f64()
            .or_else(|| row["signal_score"].as_f64())
            .unwrap_or(0.0);
        let Some(token) = row["token_address"].as_str() else {
            continue;
        };
        if score <= 0.0 {
            continue;
        }
        let token = token.to_ascii_lowercase();
        let liquidity = row["liquidity_usd"].as_f64().unwrap_or(0.0);
        if best
            .get(&token)
            .is_some_and(|(old_score, old_liquidity, _)| {
                score < *old_score || (score == *old_score && liquidity <= *old_liquidity)
            })
        {
            continue;
        }
        let reasons =
            if row["short_momentum_score"].is_null() || row["prediction_reasons"].is_null() {
                row["signal_reasons"].clone()
            } else {
                row["prediction_reasons"].clone()
            };
        best.insert(
            token.clone(),
            (
                score,
                liquidity,
                serde_json::json!({
                    "token_address":token,
                    "token_symbol":row["token_symbol"],
                    "token_name":row["token_name"],
                    "indicator_score":score,
                    "indicator_reasons":reasons,
                    "observed_at":row["observed_at"],
                    "short_signal_stale":row["short_signal_stale"],
                }),
            ),
        );
    }
    let mut items: Vec<_> = best.into_values().map(|(_, _, row)| row).collect();
    items.sort_by(|a, b| {
        b["indicator_score"]
            .as_f64()
            .partial_cmp(&a["indicator_score"].as_f64())
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                a["token_address"]
                    .as_str()
                    .cmp(&b["token_address"].as_str())
            })
    });
    items
}

fn display_overview(rows: Vec<serde_json::Value>) -> Vec<serde_json::Value> {
    let now = chrono::Utc::now();
    let rows: Vec<_> = rows
        .into_iter()
        .map(|mut row| {
            expire_short_signal(&mut row, now);
            row
        })
        .collect();
    let scored: std::collections::HashSet<String> = rows
        .iter()
        .filter(|row| {
            row["short_momentum_score"]
                .as_f64()
                .or_else(|| row["signal_score"].as_f64())
                .unwrap_or(0.0)
                > 0.0
        })
        .filter_map(|row| row["token_address"].as_str().map(str::to_ascii_lowercase))
        .collect();
    const FIELDS: &[&str] = &[
        "pair_address",
        "token_address",
        "token_symbol",
        "token_name",
        "observed_at",
        "price_usd",
        "liquidity_usd",
        "market_cap",
        "volume_h1",
        "volume_m5",
        "price_change_m5",
        "price_change_h1",
        "price_change_h24",
        "buys_h1",
        "sells_h1",
        "signal_score",
        "signal_at",
        "signal_reasons",
        "signal_risk_flags",
        "short_momentum_score",
        "prediction_reasons",
        "short_signal_stale",
    ];
    rows.into_iter()
        .filter(|row| {
            row["token_address"]
                .as_str()
                .is_some_and(|address| scored.contains(&address.to_ascii_lowercase()))
        })
        .map(|mut row| {
            if let Some(fields) = row.as_object_mut() {
                fields.retain(|key, _| FIELDS.contains(&key.as_str()));
            }
            row
        })
        .collect()
}

async fn token_oi(
    State(state): State<AppState>,
    Path(pair): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let pair = pair.to_ascii_lowercase();
    if !valid_pair(&state, &pair).await? {
        return Ok(Json(
            serde_json::json!({"available":false,"reason":"交易对不存在"}),
        ));
    }
    let (value, start) =
        cached_or_pending(&state.oi_cache, &pair, std::time::Duration::from_secs(60)).await;
    if start {
        tokio::spawn(async move {
            let result = fetch_oi(State(state.clone()), Path(pair.clone())).await;
            let mut value = result.map(|Json(v)| v).unwrap_or_else(|(_, Json(v))| {
                crate::logging::event(
                    "error",
                    "oi_refresh_failed",
                    serde_json::json!({"pair":pair,"error":v["error"]}),
                );
                serde_json::json!({"available":false,"reason":v["error"]})
            });
            value["calculated_at"] = serde_json::json!(chrono::Utc::now().to_rfc3339());
            store_result(&state.oi_cache, pair.clone(), value.clone()).await;
            persist_success(&state, "oi", &pair, &value).await;
        });
    }
    Ok(Json(value))
}

async fn fetch_oi(
    State(state): State<AppState>,
    Path(pair): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let db_path = state.db_path.as_ref().clone();
    let pair_for_lookup = pair.clone();
    let symbol =
        tokio::task::spawn_blocking(move || crate::db::futures_symbol(&db_path, &pair_for_lookup))
            .await
            .map_err(axum_error)?
            .map_err(|e| {
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(serde_json::json!({"error": e.to_string()})),
                )
            })?;
    let Some(symbol) = symbol else {
        crate::logging::event(
            "debug",
            "oi_symbol_unmatched",
            serde_json::json!({"pair":pair}),
        );
        return Ok(Json(
            serde_json::json!({"available": false, "reason": "无唯一可匹配的代币符号"}),
        ));
    };

    let client = &state.http_client;
    let base = symbol.strip_suffix("USDT").unwrap_or_default();
    if futures_symbol_trading(&state, &symbol).await == Some(false) {
        let global = global_oi_for(&state, client, base).await;
        return Ok(Json(
            serde_json::json!({"available":false,"reason":"无正在交易的 Binance USDT 永续合约","global":global}),
        ));
    }
    let request = client
        .get("https://fapi.binance.com/futures/data/openInterestHist")
        .query(&[
            ("symbol", symbol.as_str()),
            ("period", "1h"),
            ("limit", "26"),
        ])
        .send();
    let ratio_request = client
        .get("https://fapi.binance.com/futures/data/topLongShortPositionRatio")
        .query(&[
            ("symbol", symbol.as_str()),
            ("period", "1h"),
            ("limit", "1"),
        ])
        .send();
    let (global, response, ratio_response) =
        tokio::join!(global_oi_for(&state, client, base), request, ratio_request);
    let response = match response {
        Ok(response) => response,
        Err(e) => {
            crate::logging::event(
                "warn",
                "binance_oi_request_failed",
                serde_json::json!({"symbol":symbol,"error":e.to_string()}),
            );
            return Ok(Json(
                serde_json::json!({"available": false, "reason": "Binance OI 请求失败", "global":global}),
            ));
        }
    };
    if response.status() == reqwest::StatusCode::BAD_REQUEST {
        crate::logging::event(
            "debug",
            "binance_oi_not_listed",
            serde_json::json!({"symbol":symbol}),
        );
        return Ok(Json(
            serde_json::json!({"available": false, "reason": "无同名 Binance USDT 永续合约", "global":global}),
        ));
    }
    if !response.status().is_success() {
        crate::logging::event(
            "warn",
            "binance_oi_request_failed",
            serde_json::json!({"symbol":symbol,"status":response.status().as_u16()}),
        );
        return Ok(Json(
            serde_json::json!({"available": false, "reason": format!("Binance OI HTTP {}", response.status()), "global":global}),
        ));
    }
    let body: serde_json::Value = response.json().await.map_err(|e| {
        crate::logging::event(
            "warn",
            "binance_oi_decode_failed",
            serde_json::json!({"symbol":symbol,"error":e.to_string()}),
        );
        oi_upstream_error(e)
    })?;
    let Some(mut oi) = oi_from_history(&body, &symbol) else {
        crate::logging::event(
            "debug",
            "binance_oi_history_empty",
            serde_json::json!({"symbol":symbol}),
        );
        return Ok(Json(
            serde_json::json!({"available": false, "reason": "无同名合约或 OI 历史", "global":global}),
        ));
    };
    let age_ms = chrono::Utc::now().timestamp_millis() - oi["timestamp"].as_i64().unwrap_or(0);
    if !(0..=7_200_000).contains(&age_ms) {
        crate::logging::event(
            "warn",
            "binance_oi_data_stale",
            serde_json::json!({"symbol":symbol,"age_ms":age_ms}),
        );
        return Ok(Json(
            serde_json::json!({"available": false, "reason": "合约 OI 数据已过期", "global":global}),
        ));
    }
    oi["available"] = serde_json::json!(true);
    oi["global"] = serde_json::json!(global);
    match ratio_response {
        Ok(response) if response.status().is_success() => {
            match response.json::<serde_json::Value>().await {
                Ok(body) => oi["long_short"] = serde_json::json!(position_ratio(&body, &symbol)),
                Err(e) => crate::logging::event(
                    "warn",
                    "binance_ratio_decode_failed",
                    serde_json::json!({"symbol":symbol,"error":e.to_string()}),
                ),
            }
        }
        Ok(response)
            if response.status().as_u16() == 429 || response.status().is_server_error() =>
        {
            crate::logging::event(
                "warn",
                "binance_ratio_request_failed",
                serde_json::json!({"symbol":symbol,"status":response.status().as_u16()}),
            );
        }
        Err(e) => crate::logging::event(
            "warn",
            "binance_ratio_request_failed",
            serde_json::json!({"symbol":symbol,"error":e.to_string()}),
        ),
        _ => {}
    }
    Ok(Json(oi))
}

async fn token_prices(
    State(state): State<AppState>,
    Path(pair): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let pair = pair.to_ascii_lowercase();
    if !valid_pair(&state, &pair).await? {
        return Ok(Json(
            serde_json::json!({"available":false,"reason":"交易对不存在"}),
        ));
    }
    let (value, start) = cached_or_pending(
        &state.price_cache,
        &pair,
        std::time::Duration::from_secs(30),
    )
    .await;
    if start {
        tokio::spawn(async move {
            let value = fetch_market_prices(&state, &pair).await;
            store_result(&state.price_cache, pair, value).await;
        });
    }
    Ok(Json(value))
}

async fn fetch_market_prices(state: &AppState, pair: &str) -> serde_json::Value {
    let db_path = state.db_path.as_ref().clone();
    let pair_address = pair.to_string();
    let identity = tokio::task::spawn_blocking(move || {
        crate::db::market_price_identity(&db_path, &pair_address)
    })
    .await;
    let (symbol, _, pool_price) = match identity {
        Ok(Ok(Some(identity))) => identity,
        Ok(Ok(None)) => return serde_json::json!({"futures":[],"reason":"无可匹配的市场符号"}),
        error => {
            crate::logging::event(
                "error",
                "price_identity_failed",
                serde_json::json!({"pair":pair,"error":format!("{error:?}")}),
            );
            return serde_json::json!({"futures":[],"reason":"行情数据库查询失败"});
        }
    };
    if futures_symbol_trading(state, &symbol).await == Some(false) {
        return serde_json::json!({"available":false,"symbol":symbol,"futures":[],"reason":"无正在交易的 Binance USDT 永续合约"});
    }
    let futures = fetch_binance_price_history(
        &state.http_client,
        "https://fapi.binance.com/fapi/v1/markPriceKlines",
        &symbol,
        pool_price,
    )
    .await;
    match futures {
        PriceHistory::Rows(rows) => serde_json::json!({"symbol":symbol,"futures":rows}),
        PriceHistory::InvalidSymbol => {
            serde_json::json!({"available":false,"symbol":symbol,"futures":[],"reason":"无同名合约"})
        }
    }
}

#[derive(Debug)]
enum PriceHistory {
    Rows(Vec<serde_json::Value>),
    InvalidSymbol,
}

async fn fetch_binance_price_history(
    client: &reqwest::Client,
    url: &str,
    symbol: &str,
    pool_price: f64,
) -> PriceHistory {
    let response = client
        .get(url)
        .query(&[("symbol", symbol), ("interval", "1m"), ("limit", "60")])
        .send()
        .await;
    let response = match response {
        Ok(response) => response,
        Err(e) => {
            crate::logging::event(
                "warn",
                "binance_price_request_failed",
                serde_json::json!({"symbol":symbol,"error":e.to_string()}),
            );
            return PriceHistory::Rows(Vec::new());
        }
    };
    if !response.status().is_success() {
        crate::logging::event(
            "warn",
            "binance_price_request_failed",
            serde_json::json!({"symbol":symbol,"status":response.status().as_u16()}),
        );
        return if response.status() == StatusCode::BAD_REQUEST {
            PriceHistory::InvalidSymbol
        } else {
            PriceHistory::Rows(Vec::new())
        };
    }
    let body = match response.json::<serde_json::Value>().await {
        Ok(body) => body,
        Err(e) => {
            crate::logging::event(
                "warn",
                "binance_price_decode_failed",
                serde_json::json!({"symbol":symbol,"error":e.to_string()}),
            );
            return PriceHistory::Rows(Vec::new());
        }
    };
    PriceHistory::Rows(market_price_history(
        &body,
        pool_price,
        chrono::Utc::now().timestamp_millis(),
    ))
}

async fn token_depth(
    State(state): State<AppState>,
    Path(pair): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let pair = pair.to_ascii_lowercase();
    if !valid_pair(&state, &pair).await? {
        return Err((
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error":"交易对不存在"})),
        ));
    }
    let (value, start) = cached_or_pending(&state.depth_cache, &pair, DEPTH_TTL).await;
    if start {
        tokio::spawn(async move {
            let started = std::time::Instant::now();
            let _permit = state
                .depth_slots
                .acquire()
                .await
                .expect("depth semaphore closed");
            let mut value = match tokio::time::timeout(
                std::time::Duration::from_secs(45),
                fetch_depth(State(state.clone()), Path(pair.clone())),
            )
            .await
            {
                Ok(Ok(Json(v))) => v,
                Ok(Err((_, Json(v)))) => serde_json::json!({"available":false,"reason":v["error"]}),
                Err(_) => serde_json::json!({"available":false,"reason":"池子计算超时"}),
            };
            value["calculated_at"] = serde_json::json!(chrono::Utc::now().to_rfc3339());
            if value["available"] == false {
                crate::logging::event(
                    "warn",
                    "depth_refresh_failed",
                    serde_json::json!({"pair":pair,"reason":value["reason"],"elapsed_ms":started.elapsed().as_millis(),"fallback":"recent_cache_if_available"}),
                );
            }
            store_result(&state.depth_cache, pair.clone(), value.clone()).await;
            persist_success(&state, "depth", &pair, &value).await;
        });
    }
    Ok(Json(value))
}

async fn token_whales(
    State(state): State<AppState>,
    Path(pair): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let pair = pair.to_ascii_lowercase();
    if !valid_pair(&state, &pair).await? {
        return Err((
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error":"交易对不存在"})),
        ));
    }
    let permit = state.whale_slots.clone().try_acquire_owned().ok();
    if permit.is_none() {
        return Ok(Json(
            state
                .whale_cache
                .lock()
                .await
                .get(&pair)
                .and_then(|entry| entry.value.clone())
                .unwrap_or_else(|| serde_json::json!({"pending":true})),
        ));
    }
    let (value, start) = cached_or_pending(
        &state.whale_cache,
        &pair,
        whale_ttl(state.whale_priority.lock().await.contains(&pair)),
    )
    .await;
    if start {
        tokio::spawn(async move {
            let _permit = permit;
            let mut value = match fetch_whales(&state, &pair).await {
                Ok(value) => value,
                Err(reason) => serde_json::json!({"available":false,"reason":reason}),
            };
            value["calculated_at"] = serde_json::json!(chrono::Utc::now().to_rfc3339());
            if value["available"] == false {
                crate::logging::event(
                    "warn",
                    "whale_refresh_failed",
                    serde_json::json!({"provider":"geckoterminal","pair":pair,"reason":value["reason"]}),
                );
            }
            store_result(&state.whale_cache, pair.clone(), value.clone()).await;
            persist_success(&state, "whales", &pair, &value).await;
        });
    }
    Ok(Json(value))
}

async fn gecko_request_with_fallback(
    direct: &reqwest::Client,
    proxies: &[GeckoProxy],
    cursor: &AtomicUsize,
    url: &str,
) -> (Result<reqwest::Response, reqwest::Error>, bool, u8) {
    let start = cursor.fetch_add(1, Ordering::Relaxed);
    let mut attempts = 0;
    let mut rate_limits = 0;
    for offset in 0..proxies.len() {
        let index = (start + offset) % proxies.len();
        let proxy = &proxies[index];
        if *proxy.retry_at.lock().await > std::time::Instant::now() {
            continue;
        }
        attempts += 1;
        let response = proxy
            .client
            .get(url)
            .query(&[("trade_volume_in_usd_greater_than", "10000")])
            .send()
            .await;
        if response
            .as_ref()
            .is_ok_and(|response| response.status() == reqwest::StatusCode::TOO_MANY_REQUESTS)
        {
            rate_limits += 1;
        }
        let retry = match &response {
            Ok(response) if response.status() == reqwest::StatusCode::TOO_MANY_REQUESTS => response
                .headers()
                .get(reqwest::header::RETRY_AFTER)
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(60)
                .clamp(60, 3600),
            Ok(response)
                if response.status().is_server_error()
                    || response.status() == reqwest::StatusCode::FORBIDDEN =>
            {
                60
            }
            Err(_) => 60,
            _ => {
                crate::logging::event(
                    "info",
                    "whale_proxy_request_completed",
                    serde_json::json!({"proxy_index":index,"status":response.as_ref().ok().map(|response|response.status().as_u16())}),
                );
                return (response, true, rate_limits);
            }
        };
        *proxy.retry_at.lock().await =
            std::time::Instant::now() + std::time::Duration::from_secs(retry);
        crate::logging::event(
            "warn",
            "whale_proxy_failed",
            serde_json::json!({"proxy_index":index,"cooldown_seconds":retry,"status":response.as_ref().ok().map(|response|response.status().as_u16()),"error":response.as_ref().err().map(|error|error.to_string())}),
        );
        if attempts >= 3 {
            break;
        }
    }
    if !proxies.is_empty() {
        crate::logging::event(
            "warn",
            "whale_proxy_fallback_direct",
            serde_json::json!({"attempts":attempts}),
        );
    }
    (
        direct
            .get(url)
            .query(&[("trade_volume_in_usd_greater_than", "10000")])
            .send()
            .await,
        false,
        rate_limits,
    )
}

async fn fetch_whales(state: &AppState, pair: &str) -> Result<serde_json::Value, String> {
    let path = state.db_path.as_ref().clone();
    let pair_owned = pair.to_string();
    let row = tokio::task::spawn_blocking(move || crate::db::pair_for_depth(&path, &pair_owned))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "交易对不存在".to_string())?;
    let token = row["token_address"].as_str().ok_or("代币地址缺失")?;
    // ponytail: one-process spacing leaves room below GeckoTerminal's documented 30/minute limit; use a shared limiter if multiple servers are deployed.
    let _gate = state
        .gecko_request_gate
        .acquire()
        .await
        .map_err(|e| e.to_string())?;
    let wait = state
        .gecko_next
        .lock()
        .await
        .next
        .saturating_duration_since(std::time::Instant::now());
    tokio::time::sleep(wait).await;
    let url = format!("https://api.geckoterminal.com/api/v2/networks/bsc/pools/{pair}/trades");
    let (response, _, proxy_rate_limits) = gecko_request_with_fallback(
        &state.http_client,
        &state.gecko_proxies,
        &state.gecko_proxy_cursor,
        &url,
    )
    .await;
    let retry_after = response.as_ref().ok().and_then(|response| {
        response
            .headers()
            .get(reqwest::header::RETRY_AFTER)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.parse::<u64>().ok())
    });
    let mut pacer = state.gecko_next.lock().await;
    let previous_spacing = pacer.spacing_secs;
    let cooldown = pacer.after_response(
        response.as_ref().ok().map(reqwest::Response::status),
        retry_after,
        proxy_rate_limits,
    );
    if pacer.spacing_secs != previous_spacing {
        crate::logging::event(
            "info",
            "whale_pacer_adjusted",
            serde_json::json!({"spacing_seconds":pacer.spacing_secs,"recent_rate_limits":pacer.recent_limits.len()}),
        );
    }
    let response = response.map_err(|e| format!("{e:?}"))?;
    if let Some(cooldown) = cooldown {
        crate::logging::event(
            "warn",
            "whale_provider_rate_limited",
            serde_json::json!({"provider":"geckoterminal","cooldown_seconds":cooldown,"spacing_seconds":pacer.spacing_secs}),
        );
        return Err("交易数据源限流，稍后自动重试".into());
    }
    drop(pacer);
    let response = response.error_for_status().map_err(|e| e.to_string())?;
    let body: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    if !body["data"].is_array() {
        return Err("交易数据格式错误".into());
    }
    Ok(serde_json::json!({"available":true,"trades":whale_trades(&body, token)}))
}

async fn token_transfers(
    State(state): State<AppState>,
    Path(pair): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let pair = pair.to_ascii_lowercase();
    if !valid_pair(&state, &pair).await? {
        return Err((
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error":"交易对不存在"})),
        ));
    }
    let (value, start) = cached_or_pending(
        &state.transfer_cache,
        &pair,
        std::time::Duration::from_secs(120),
    )
    .await;
    if start {
        tokio::spawn(async move {
            let mut value = match fetch_transfers(&state, &pair).await {
                Ok(value) => value,
                Err(reason) => serde_json::json!({"available":false,"reason":reason}),
            };
            value["calculated_at"] = serde_json::json!(chrono::Utc::now().to_rfc3339());
            if value["available"] == false {
                crate::logging::event(
                    "warn",
                    "transfer_refresh_failed",
                    serde_json::json!({"provider":"binplorer","pair":pair,"reason":value["reason"]}),
                );
            }
            store_result(&state.transfer_cache, pair, value).await;
        });
    }
    Ok(Json(value))
}

async fn fetch_transfers(state: &AppState, pair: &str) -> Result<serde_json::Value, String> {
    let path = state.db_path.as_ref().clone();
    let pair_owned = pair.to_string();
    let row = tokio::task::spawn_blocking(move || crate::db::pair_for_depth(&path, &pair_owned))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "交易对不存在".to_string())?;
    let token = row["token_address"].as_str().ok_or("代币地址缺失")?;
    if !valid_hex(token, 42) {
        return Err("代币地址无效".into());
    }
    let response = state
        .http_client
        .get(format!("https://api.binplorer.com/getTokenHistory/{token}"))
        .query(&[("apiKey", "freekey"), ("limit", "100")])
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?;
    let body: serde_json::Value = response.json().await.map_err(|e| e.to_string())?;
    let Some(operations) = body["operations"].as_array() else {
        return Err("转账数据格式错误".into());
    };
    Ok(
        serde_json::json!({"available":true,"sample_count":operations.len(),"transfers":large_transfers(&body, token)}),
    )
}

async fn valid_pair(
    state: &AppState,
    pair: &str,
) -> Result<bool, (StatusCode, Json<serde_json::Value>)> {
    if pair.len() != 42
        || !pair.starts_with("0x")
        || !pair[2..].bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Ok(false);
    }
    let path = state.db_path.as_ref().clone();
    let pair = pair.to_string();
    tokio::task::spawn_blocking(move || crate::db::pair_for_depth(&path, &pair))
        .await
        .map_err(axum_error)?
        .map(|row| row.is_some())
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error":e.to_string()})),
            )
        })
}

async fn fetch_depth(
    State(state): State<AppState>,
    Path(pair): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let path = state.db_path.as_ref().clone();
    let row = tokio::task::spawn_blocking(move || crate::db::pair_for_depth(&path, &pair))
        .await
        .map_err(axum_error)?
        .map_err(|e| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error":e.to_string()})),
            )
        })?;
    let Some(row) = row else {
        return Err((
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error":"交易对不存在"})),
        ));
    };
    let value = crate::depth::calculate(&row).await;
    Ok(Json(value))
}

fn priority_freshness(
    cache: &HashMap<String, CacheEntry>,
    pairs: &[String],
    now: chrono::DateTime<chrono::Utc>,
) -> (usize, usize) {
    let (mut old, mut missing) = (0, 0);
    for pair in pairs {
        let at = cache
            .get(pair)
            .and_then(|entry| entry.value.as_ref())
            .and_then(|value| {
                if value["available"] != true {
                    return None;
                }
                value["calculated_at"]
                    .as_str()?
                    .parse::<chrono::DateTime<chrono::FixedOffset>>()
                    .ok()
            });
        match at {
            Some(at)
                if now
                    .signed_duration_since(at.with_timezone(&chrono::Utc))
                    .num_seconds()
                    > 300 =>
            {
                old += 1
            }
            Some(_) => {}
            None => missing += 1,
        }
    }
    (old, missing)
}

fn result_age_seconds(
    value: &serde_json::Value,
    now: chrono::DateTime<chrono::Utc>,
) -> Option<u64> {
    let at = value["calculated_at"]
        .as_str()?
        .parse::<chrono::DateTime<chrono::FixedOffset>>()
        .ok()?;
    u64::try_from(
        now.signed_duration_since(at.with_timezone(&chrono::Utc))
            .num_seconds(),
    )
    .ok()
}

fn oi_unsupported(value: &serde_json::Value) -> bool {
    value["available"] == false
        && matches!(
            value["reason"].as_str(),
            Some(
                "无唯一可匹配的代币符号"
                    | "无正在交易的 Binance USDT 永续合约"
                    | "无同名 Binance USDT 永续合约"
                    | "无同名合约或 OI 历史"
            )
        )
}

async fn status(
    State(state): State<AppState>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let db_path = state.db_path.as_ref().clone();
    let result = tokio::task::spawn_blocking(move || crate::db::status(&db_path))
        .await
        .map_err(axum_error)?;
    match result {
        Ok(mut v) => {
            v["detail_jobs"] = serde_json::json!({});
            let whale_priority = state.whale_priority.lock().await.clone();
            for (name, cache) in [
                ("oi", &state.oi_cache),
                ("depth", &state.depth_cache),
                ("whales", &state.whale_cache),
            ] {
                let entries = cache.lock().await;
                let now = chrono::Utc::now();
                let success_ages: Vec<u64> = entries
                    .values()
                    .filter_map(|entry| entry.value.as_ref())
                    .filter(|value| value["available"] == true)
                    .filter_map(|value| result_age_seconds(value, now))
                    .collect();
                let unsupported = if name == "oi" {
                    entries
                        .values()
                        .filter(|entry| entry.value.as_ref().is_some_and(oi_unsupported))
                        .count()
                } else {
                    0
                };
                let unavailable = entries
                    .values()
                    .filter(|entry| {
                        entry
                            .value
                            .as_ref()
                            .is_some_and(|value| value["available"] == false)
                    })
                    .count();
                v["detail_jobs"][name] = serde_json::json!({
                    "cached": entries.values().filter(|entry| entry.value.is_some()).count(),
                    "loading": entries.values().filter(|entry| entry.loading).count(),
                    "stale": entries.values().filter(|entry| entry.value.as_ref().is_some_and(|v| v["stale"] == true)).count(),
                    "unavailable": unavailable,
                    "unsupported": unsupported,
                    "failed": unavailable - unsupported,
                    "successful_over_5m": success_ages.iter().filter(|age| **age > 300).count(),
                    "oldest_success_seconds": success_ages.into_iter().max().unwrap_or(0),
                });
                if name == "whales" {
                    let (over_5m, missing) =
                        priority_freshness(&*entries, &whale_priority, chrono::Utc::now());
                    v["detail_jobs"][name]["priority_total"] =
                        serde_json::json!(whale_priority.len());
                    v["detail_jobs"][name]["priority_over_5m"] = serde_json::json!(over_5m);
                    v["detail_jobs"][name]["priority_missing"] = serde_json::json!(missing);
                }
            }
            Ok(Json(v))
        }
        Err(e) => Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": e.to_string()})),
        )),
    }
}

async fn token_snapshots(
    State(state): State<AppState>,
    Path(pair): Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let limit = params
        .get("limit")
        .and_then(|v| v.parse().ok())
        .unwrap_or(200)
        .min(1000);
    let db_path = state.db_path.as_ref().clone();
    let result = tokio::task::spawn_blocking(move || crate::db::snapshots(&db_path, &pair, limit))
        .await
        .map_err(axum_error)?;
    match result {
        Ok(rows) => Ok(Json(serde_json::Value::Array(rows))),
        Err(e) => Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": e.to_string()})),
        )),
    }
}

async fn token_signals(
    State(state): State<AppState>,
    Path(pair): Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let limit = params
        .get("limit")
        .and_then(|v| v.parse().ok())
        .unwrap_or(50)
        .min(200);
    let db_path = state.db_path.as_ref().clone();
    let result = tokio::task::spawn_blocking(move || crate::db::signals(&db_path, &pair, limit))
        .await
        .map_err(axum_error)?;
    match result {
        Ok(rows) => Ok(Json(serde_json::Value::Array(rows))),
        Err(e) => Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": e.to_string()})),
        )),
    }
}

async fn health_check() -> Json<serde_json::Value> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .no_proxy()
        .build();

    let mut result = serde_json::json!({"timestamp": chrono::Utc::now().to_rfc3339()});

    if let Ok(c) = client {
        match c.get("https://api.dexscreener.com/token-pairs/v1/bsc/0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c")
            .send()
            .await
        {
            Ok(resp) => {
                result["dexscreener"] = serde_json::json!({"ok": resp.status().is_success(), "status": resp.status().as_u16()});
            }
            Err(e) => {
                let mut chain = vec![e.to_string()];
                let mut src = e.source();
                while let Some(s) = src {
                    chain.push(s.to_string());
                    src = s.source();
                }
                result["dexscreener"] = serde_json::json!({"ok": false, "error": chain});
            }
        }
    }

    Json(result)
}

fn oi_upstream_error(e: reqwest::Error) -> (StatusCode, Json<serde_json::Value>) {
    (
        StatusCode::BAD_GATEWAY,
        Json(serde_json::json!({"error": e.to_string()})),
    )
}

fn axum_error(e: tokio::task::JoinError) -> (StatusCode, Json<serde_json::Value>) {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(serde_json::json!({"error": format!("task failed: {}", e)})),
    )
}
