//! Main worker loop: Binance Alpha sync → DexScreener search → signal → SQLite.

use super::features::{build_feature_vector, FeatureThresholds, PairSnapshot};
use super::signals::{evaluate, SignalConfig};
use super::{signal_v2, signal_v2_store};
use crate::clients::binance_alpha::BinanceAlphaClient;
use crate::clients::dexscreener::DexScreenerClient;
use crate::config::{Config, SignalV2Mode};
use crate::db;
use serde_json::Value;
use std::collections::HashSet;
use std::time::Duration;

pub async fn run_worker(db_path: &str) {
    let config = Config::from_env();
    let dex = DexScreenerClient::new();
    let alpha = BinanceAlphaClient::new(&config.binance_alpha_url);
    let mut alpha_cache: Option<Vec<crate::clients::binance_alpha::AlphaToken>> = None;
    let mut next_alpha_refresh = std::time::Instant::now();
    // ponytail: retry queue is process-local; persist it if failed tokens must survive restart without a full scan.
    let mut dex_failures = std::collections::HashMap::new();
    let mut dex_successes = std::collections::HashMap::new();
    let mut shadow_retry_at = std::time::Instant::now();

    if let Err(e) = db::init_schema(db_path) {
        crate::logging::event(
            "error",
            "worker_db_init_failed",
            serde_json::json!({"error":e.to_string()}),
        );
        return;
    }

    crate::logging::event(
        "info",
        "worker_started",
        serde_json::json!({"loop_seconds":config.worker_loop_seconds,"batch_size":config.max_pairs_per_cycle,"signal_v2_mode":config.signal_v2_mode.as_str(),"source_time_unverified":true}),
    );

    let cleanup_path = db_path.to_string();
    tokio::spawn(async move {
        loop {
            let path = cleanup_path.clone();
            let wait = match tokio::task::spawn_blocking(move || db::prune_history(&path)).await {
                Ok(Ok(true)) => 5,
                Ok(Ok(false)) => 86_400,
                error => {
                    crate::logging::event(
                        "error",
                        "history_cleanup_failed",
                        serde_json::json!({"error":format!("{error:?}")}),
                    );
                    30
                }
            };
            tokio::time::sleep(Duration::from_secs(wait)).await;
        }
    });

    loop {
        let start = std::time::Instant::now();
        if std::time::Instant::now() >= next_alpha_refresh {
            next_alpha_refresh = std::time::Instant::now()
                + Duration::from_secs(config.binance_alpha_refresh_minutes.max(1) * 60);
            match alpha.fetch_bsc_tokens().await {
                Ok(tokens) if !tokens.is_empty() => {
                    if let Some(symbols) = binance_futures_symbols().await {
                        let listed = futures_listed_tokens(tokens, &symbols);
                        if !listed.is_empty() {
                            let active: Vec<String> =
                                listed.iter().map(|token| token.address.clone()).collect();
                            let retired = match db::retire_missing_alpha_tokens(db_path, &active) {
                                Ok(retired) => retired,
                                Err(error) => {
                                    crate::logging::event(
                                        "error",
                                        "alpha_retire_failed",
                                        serde_json::json!({"error":error.to_string()}),
                                    );
                                    0
                                }
                            };
                            crate::logging::event(
                                "info",
                                "alpha_refreshed",
                                serde_json::json!({"tokens":listed.len(),"retired_pools":retired}),
                            );
                            alpha_cache = Some(listed);
                        } else {
                            crate::logging::event(
                                "warn",
                                "binance_futures_alpha_empty",
                                serde_json::json!({"fallback":"previous_alpha_list"}),
                            );
                        }
                    } else {
                        crate::logging::event(
                            "warn",
                            "binance_futures_catalog_failed",
                            serde_json::json!({"fallback":"previous_alpha_list"}),
                        );
                    }
                }
                Ok(_) => crate::logging::event(
                    "warn",
                    "alpha_refresh_failed",
                    serde_json::json!({"reason":"empty BSC list","fallback":"previous_list"}),
                ),
                Err(e) => crate::logging::event(
                    "warn",
                    "alpha_refresh_failed",
                    serde_json::json!({"error":e.to_string(),"fallback":"previous_list"}),
                ),
            }
        }
        let Some(tokens) = alpha_cache.as_ref() else {
            tokio::time::sleep(Duration::from_secs(config.worker_loop_seconds.max(15))).await;
            continue;
        };
        match run_cycle(
            db_path,
            &config,
            &dex,
            tokens,
            &mut dex_failures,
            &mut dex_successes,
            &mut shadow_retry_at,
        )
        .await
        {
            Ok((tokens, pairs_processed, priority_due)) => {
                crate::logging::event(
                    "info",
                    "market_cycle_completed",
                    serde_json::json!({"alpha_tokens":tokens,"pairs_processed":pairs_processed,"priority_due":priority_due,"retry_pending":dex_failures.len(),"elapsed_ms":start.elapsed().as_millis()}),
                );
            }
            Err(e) => {
                crate::logging::event(
                    "error",
                    "market_cycle_failed",
                    serde_json::json!({"error":e.to_string(),"elapsed_ms":start.elapsed().as_millis()}),
                );
            }
        }
        let elapsed = start.elapsed();
        let sleep_for = config.worker_loop_seconds.saturating_sub(elapsed.as_secs());
        tokio::time::sleep(Duration::from_secs(sleep_for.max(1))).await;
    }
}

async fn binance_futures_symbols() -> Option<HashSet<String>> {
    let body = reqwest::get("https://fapi.binance.com/fapi/v1/exchangeInfo")
        .await
        .ok()?
        .json::<Value>()
        .await
        .ok()?;
    crate::dashboard::parse_trading_futures_symbols(&body)
}

fn futures_listed_tokens(
    tokens: Vec<crate::clients::binance_alpha::AlphaToken>,
    symbols: &HashSet<String>,
) -> Vec<crate::clients::binance_alpha::AlphaToken> {
    tokens
        .into_iter()
        .filter(|token| symbols.contains(&format!("{}USDT", token.symbol.to_uppercase())))
        .collect()
}

async fn run_cycle(
    db_path: &str,
    config: &Config,
    dex: &DexScreenerClient,
    tokens: &[crate::clients::binance_alpha::AlphaToken],
    failures: &mut std::collections::HashMap<String, (u8, std::time::Instant)>,
    successes: &mut std::collections::HashMap<String, std::time::Instant>,
    shadow_retry_at: &mut std::time::Instant,
) -> Result<(usize, usize, usize), Box<dyn std::error::Error + Send + Sync>> {
    // Continue from the previous batch, wrapping to the start of the Alpha list.
    const CURSOR: &str = "binance_alpha_bsc_last_token";
    let cursor = db::scan_cursor(db_path, CURSOR)?;
    let start = scan_start(&tokens, cursor.as_deref());
    let batch_size = config.max_pairs_per_cycle.min(tokens.len());
    let short_priority = db::short_cycle_priority_tokens(db_path)?;
    let visible_rows = crate::dashboard::list_overview(db::overview(db_path, 5000)?);
    let visible: Vec<String> = visible_rows
        .iter()
        .filter_map(|row| row["token_address"].as_str().map(str::to_string))
        .collect();
    let hot: Vec<String> = visible.iter().take(50).cloned().collect();
    let mut pairs_processed = 0;
    let mut dex_requests = 0;
    let mut dex_elapsed_ms = 0;
    let mut dex_max_ms = 0;
    let mut last_scanned = None;
    let ttl = Duration::from_secs(180);
    successes.retain(|_, at| at.elapsed() < ttl);
    let mut priority = Vec::new();
    for address in short_priority.iter().chain(&visible) {
        let max_age = market_refresh_age(address, &short_priority, &hot, &visible);
        if priority.contains(address)
            || failures.contains_key(address)
            || !tokens.iter().any(|token| token.address == *address)
            || successes
                .get(address)
                .is_some_and(|at| at.elapsed() < Duration::from_secs(max_age))
            || db::token_snapshot_recent(db_path, address, max_age as i64)?
        {
            continue;
        }
        priority.push(address.clone());
    }
    let priority_due = priority_slots(batch_size, priority.len());
    let normal_slots = batch_size - priority_due;
    let now = chrono::Utc::now();
    let mut stale_hot = 0;
    let mut stale_other = 0;
    let mut max_hot_age = 0;
    let mut max_other_age = 0;
    for (index, row) in visible_rows.iter().enumerate() {
        let Some(age) = row["observed_at"]
            .as_str()
            .and_then(|at| chrono::DateTime::parse_from_rfc3339(at).ok())
            .map(|at| (now - at.with_timezone(&chrono::Utc)).num_seconds().max(0) as u64)
        else {
            continue;
        };
        if index < 50 {
            stale_hot += usize::from(age > 300);
            max_hot_age = max_hot_age.max(age);
        } else {
            stale_other += usize::from(age > 300);
            max_other_age = max_other_age.max(age);
        }
    }
    crate::logging::event(
        "info",
        "market_refresh_plan",
        serde_json::json!({"visible":visible.len(),"hot_over_5m":stale_hot,"other_over_5m":stale_other,"hot_max_age_seconds":max_hot_age,"other_max_age_seconds":max_other_age,"priority_slots":priority_due,"discovery_slots":normal_slots}),
    );
    let work = scan_work(tokens, start, batch_size, failures, &priority);
    let normal_start = work.len() - normal_slots;
    for (position, (index, retry)) in work.into_iter().enumerate() {
        let token = &tokens[index];
        let max_age = market_refresh_age(&token.address, &short_priority, &hot, &visible);
        if !retry {
            if position >= normal_start {
                last_scanned = Some(token.address.as_str());
            }
            if failures.contains_key(&token.address) {
                continue;
            }
        }
        if successes
            .get(&token.address)
            .is_some_and(|at| at.elapsed() < Duration::from_secs(max_age))
            || db::token_snapshot_recent(db_path, &token.address, max_age as i64)?
        {
            if retry {
                failures.remove(&token.address);
            }
            continue;
        }
        let request_started = std::time::Instant::now();
        let result = dex.pairs_for_token(&token.address).await;
        let elapsed_ms = request_started.elapsed().as_millis();
        dex_requests += 1;
        dex_elapsed_ms += elapsed_ms;
        dex_max_ms = dex_max_ms.max(elapsed_ms);
        match result {
            Ok(pairs) => {
                failures.remove(&token.address);
                let now = chrono::Utc::now();
                let selected: Vec<_> = selected_pools(&pairs)
                    .into_iter()
                    .filter_map(|pair| {
                        snapshot_from_pair(token, pair, &now.to_rfc3339())
                            .map(|snapshot| (pair, snapshot))
                    })
                    .collect();
                let snapshots: Vec<_> = selected
                    .iter()
                    .map(|(_, snapshot)| snapshot.clone())
                    .collect();
                let addresses: Vec<_> = snapshots
                    .iter()
                    .map(|snapshot| snapshot.pair_address.clone())
                    .collect();
                let previous = match db::prior_pool_liquidity(
                    db_path,
                    &token.address,
                    &addresses,
                    now,
                ) {
                    Ok(value) => value,
                    Err(error) => {
                        crate::logging::event(
                            "warn",
                            "prior_liquidity_unavailable",
                            serde_json::json!({"token":token.address,"error":error.to_string()}),
                        );
                        None
                    }
                };
                let decision = aggregate_snapshots(&snapshots).map(|aggregate| {
                    let mut decision = score_token(config, token, &aggregate);
                    if previous.is_some_and(|prior| {
                        prior > 0.0 && aggregate.liquidity_usd.unwrap_or(0.0) <= prior * 0.75
                    }) {
                        decision.risk_flags.push("liquidity_withdrawal".into());
                    }
                    decision
                });
                let mut current = Vec::new();
                let mut failed = false;
                let shadow_samples: Vec<_> = if config.signal_v2_mode == SignalV2Mode::Shadow {
                    selected
                        .iter()
                        .map(|(pair, snapshot)| {
                            signal_v2_store::sample(
                                snapshot,
                                &signal_v2_store::metadata(pair, &snapshot.observed_at),
                            )
                            .unwrap_or_else(|| signal_v2::Sample {
                                at: now.timestamp(),
                                pair_address: snapshot.pair_address.clone(),
                                price: None,
                                volume_h1: None,
                                volume_m5: None,
                                liquidity_usd: None,
                                fingerprint: String::new(),
                            })
                        })
                        .collect()
                } else {
                    Vec::new()
                };
                for (pair_data, snapshot) in selected {
                    let db2 = db_path.to_string();
                    let Some(decision) = decision.clone() else {
                        break;
                    };
                    let result = tokio::task::spawn_blocking({
                        let token = token.clone();
                        let pair_data = pair_data.clone();
                        move || write_scored_pair(&db2, &token, &pair_data, &snapshot, &decision)
                    })
                    .await;
                    match result {
                        Ok(Ok(_)) => {
                            pairs_processed += 1;
                            if let Some(address) = pair_data["pairAddress"].as_str() {
                                current.push(address.to_ascii_lowercase());
                            }
                        }
                        error => {
                            failed = true;
                            crate::logging::event(
                                "error",
                                "pool_write_failed",
                                serde_json::json!({"token":token.address,"pair":pair_data["pairAddress"],"error":format!("{error:?}")}),
                            );
                        }
                    }
                }
                if !failed && !current.is_empty() {
                    if let Err(e) = db::retire_missing_pools(db_path, &token.address, &current) {
                        failed = true;
                        crate::logging::event(
                            "error",
                            "pool_retirement_failed",
                            serde_json::json!({"token":token.address,"error":e.to_string()}),
                        );
                    }
                }
                if !failed {
                    successes.insert(token.address.clone(), std::time::Instant::now());
                    if config.signal_v2_mode == SignalV2Mode::Shadow
                        && std::time::Instant::now() >= *shadow_retry_at
                    {
                        let path = db_path.to_string();
                        let address = token.address.clone();
                        let at = now.to_rfc3339();
                        let settings = signal_v2::Settings {
                            min_volume_h1_usd: config.min_volume_h1_usd,
                            focus_score_threshold: config.focus_score_threshold,
                        };
                        let score = decision.as_ref().map_or(0, |d| d.score);
                        let risks = decision
                            .as_ref()
                            .map_or_else(Vec::new, |d| d.risk_flags.clone());
                        let result = tokio::task::spawn_blocking(move || {
                            signal_v2_store::observe(
                                &path,
                                &address,
                                &shadow_samples,
                                &at,
                                score,
                                &risks,
                                &settings,
                            )
                        })
                        .await;
                        if !matches!(result, Ok(Ok(_))) {
                            *shadow_retry_at = std::time::Instant::now() + Duration::from_secs(60);
                            crate::logging::event(
                                "warn",
                                "signal_v2_shadow_failed",
                                serde_json::json!({"token":token.address,"error":format!("{result:?}"),"retry_seconds":60}),
                            );
                        }
                    }
                }
            }
            Err(e) => {
                let attempt = failures
                    .get(&token.address)
                    .map_or(1, |(count, _)| count.saturating_add(1));
                let delay = dex_retry_delay(attempt);
                let rate_limited = e.to_string().contains("429");
                crate::logging::event(
                    "warn",
                    "dex_token_failed",
                    serde_json::json!({"provider":"dexscreener","token":token.address,"error":e.to_string(),"attempt":attempt,"retry_seconds":delay.as_secs(),"rate_limited":rate_limited}),
                );
                failures.insert(
                    token.address.clone(),
                    (attempt, std::time::Instant::now() + delay),
                );
                if rate_limited {
                    // shortcut: fixed cooldown after rate limiting; use Retry-After if the API starts sending it.
                    tokio::time::sleep(Duration::from_secs(60)).await;
                    break;
                }
            }
        }
        // Keep requests under DexScreener's 300/minute token-pairs limit.
        tokio::time::sleep(Duration::from_millis(250)).await;
    }

    if let Some(address) = last_scanned {
        db::set_scan_cursor(db_path, CURSOR, address)?;
    }
    crate::logging::event(
        "info",
        "market_provider_batch",
        serde_json::json!({"provider":"dexscreener","requests":dex_requests,"elapsed_ms":dex_elapsed_ms,"max_request_ms":dex_max_ms}),
    );

    Ok((tokens.len(), pairs_processed, priority_due))
}

fn dex_retry_delay(attempt: u8) -> Duration {
    Duration::from_secs((15_u64 << attempt.min(5)).min(300))
}

fn market_refresh_age(address: &str, short: &[String], hot: &[String], visible: &[String]) -> u64 {
    if short.iter().any(|token| token == address) || hot.iter().any(|token| token == address) {
        20
    } else if visible.iter().any(|token| token == address) {
        30
    } else {
        90
    }
}

fn priority_slots(batch_size: usize, due_count: usize) -> usize {
    due_count.min(batch_size.saturating_sub((batch_size / 4).max(1)))
}

fn scan_work(
    tokens: &[crate::clients::binance_alpha::AlphaToken],
    start: usize,
    batch_size: usize,
    failures: &std::collections::HashMap<String, (u8, std::time::Instant)>,
    priority: &[String],
) -> Vec<(usize, bool)> {
    let mut due: Vec<_> = failures
        .iter()
        .filter(|(_, (_, at))| *at <= std::time::Instant::now())
        .filter_map(|(address, (_, at))| {
            tokens
                .iter()
                .position(|token| token.address == *address)
                .map(|index| (index, *at))
        })
        .collect();
    due.sort_by_key(|(_, at)| *at);
    let prioritized = priority_slots(batch_size, priority.len());
    due.into_iter()
        .take(10)
        .map(|(index, _)| (index, true))
        .chain(priority.iter().take(prioritized).filter_map(|address| {
            tokens
                .iter()
                .position(|token| token.address == *address)
                .map(|index| (index, false))
        }))
        .chain((0..batch_size - prioritized).map(|offset| ((start + offset) % tokens.len(), false)))
        .collect()
}

fn scan_start(tokens: &[crate::clients::binance_alpha::AlphaToken], cursor: Option<&str>) -> usize {
    tokens
        .iter()
        .position(|token| Some(token.address.as_str()) == cursor)
        .map_or(0, |index| (index + 1) % tokens.len())
}

fn selected_pools(pairs: &[Value]) -> Vec<&Value> {
    let mut count = std::collections::HashMap::new();
    pairs
        .iter()
        .filter(|pair| {
            let Some(dex) = pair["dexId"].as_str() else {
                return false;
            };
            if !matches!(dex, "pancakeswap" | "uniswap") {
                return false;
            }
            // ponytail: only the three largest pools per DEX keep refresh and depth costs bounded; raise the cap if coverage proves insufficient.
            let seen = count.entry(dex).or_insert(0);
            *seen += 1;
            *seen <= 3
        })
        .collect()
}

fn aggregate_snapshots(snapshots: &[PairSnapshot]) -> Option<PairSnapshot> {
    let mut result = snapshots
        .iter()
        .max_by(|a, b| {
            a.liquidity_usd
                .unwrap_or(0.0)
                .total_cmp(&b.liquidity_usd.unwrap_or(0.0))
        })?
        .clone();
    let liquidity = snapshots
        .iter()
        .map(|s| s.liquidity_usd.unwrap_or(0.0).max(0.0))
        .sum::<f64>();
    let priced = snapshots
        .iter()
        .filter(|s| s.price_usd.is_some_and(|p| p > 0.0));
    let price_weight = priced
        .clone()
        .map(|s| s.liquidity_usd.unwrap_or(0.0).max(0.0))
        .sum::<f64>();
    result.price_usd = (price_weight > 0.0).then(|| {
        priced
            .clone()
            .map(|s| s.price_usd.unwrap() * s.liquidity_usd.unwrap_or(0.0).max(0.0))
            .sum::<f64>()
            / price_weight
    });
    result.liquidity_usd = Some(liquidity);
    result.volume_m5 = snapshots.iter().map(|s| s.volume_m5).sum();
    result.volume_h1 = snapshots.iter().map(|s| s.volume_h1).sum();
    result.volume_h24 = snapshots.iter().map(|s| s.volume_h24).sum();
    result.buys_m5 = snapshots
        .iter()
        .fold(0_i32, |n, s| n.saturating_add(s.buys_m5));
    result.sells_m5 = snapshots
        .iter()
        .fold(0_i32, |n, s| n.saturating_add(s.sells_m5));
    result.buys_h1 = snapshots
        .iter()
        .fold(0_i32, |n, s| n.saturating_add(s.buys_h1));
    result.sells_h1 = snapshots
        .iter()
        .fold(0_i32, |n, s| n.saturating_add(s.sells_h1));
    if liquidity > 0.0 {
        let weighted = |value: fn(&PairSnapshot) -> f64| {
            snapshots
                .iter()
                .map(|s| value(s) * s.liquidity_usd.unwrap_or(0.0).max(0.0))
                .sum::<f64>()
                / liquidity
        };
        result.price_change_m5 = weighted(|s| s.price_change_m5);
        result.price_change_h1 = weighted(|s| s.price_change_h1);
        result.price_change_h24 = weighted(|s| s.price_change_h24);
    }
    Some(result)
}

fn snapshot_from_pair(
    token: &crate::clients::binance_alpha::AlphaToken,
    pair_data: &Value,
    now: &str,
) -> Option<PairSnapshot> {
    let pair_address = pair_data
        .get("pairAddress")
        .and_then(|p| p.as_str())
        .unwrap_or_default()
        .to_lowercase();
    let token_address = token.address.clone();

    if pair_address.is_empty() {
        return None;
    }

    // Extract snapshot fields from DexScreener
    let price_usd = pair_data
        .get("priceUsd")
        .and_then(|p| p.as_str())
        .and_then(|s| s.parse::<f64>().ok())
        .or_else(|| pair_data.get("priceUsd").and_then(|p| p.as_f64()));
    let liquidity_usd = pair_data
        .get("liquidity")
        .and_then(|l| l.get("usd"))
        .and_then(|u| u.as_f64());
    let fdv = pair_data.get("fdv").and_then(|f| f.as_f64());
    let market_cap = pair_data.get("marketCap").and_then(|m| m.as_f64());

    let vol = |key: &str| {
        pair_data
            .get("volume")
            .and_then(|v| v.get(key))
            .and_then(|x| x.as_f64())
            .unwrap_or(0.0)
    };
    let tx = |period: &str, side: &str| {
        pair_data
            .get("txns")
            .and_then(|t| t.get(period))
            .and_then(|p| p.get(side))
            .and_then(|x| x.as_i64())
            .unwrap_or(0) as i32
    };
    let change = |key: &str| {
        pair_data
            .get("priceChange")
            .and_then(|p| p.get(key))
            .and_then(|x| x.as_f64())
            .unwrap_or(0.0)
    };

    let pair_created_at = pair_data
        .get("pairCreatedAt")
        .and_then(|t| t.as_i64())
        .map(|ts| {
            chrono::DateTime::from_timestamp(ts / 1000, 0)
                .map(|d| d.to_rfc3339())
                .unwrap_or_default()
        })
        .unwrap_or_else(|| now.to_string());

    Some(PairSnapshot {
        pair_address: pair_address.clone(),
        token_address: token_address.clone(),
        observed_at: now.to_string(),
        pair_created_at,
        price_usd,
        price_native: None,
        liquidity_usd,
        fdv,
        market_cap,
        volume_m5: vol("m5"),
        volume_h1: vol("h1"),
        volume_h24: vol("h24"),
        buys_m5: tx("m5", "buys"),
        sells_m5: tx("m5", "sells"),
        buys_h1: tx("h1", "buys"),
        sells_h1: tx("h1", "sells"),
        price_change_m5: change("m5"),
        price_change_h1: change("h1"),
        price_change_h24: change("h24"),
        website_count: pair_data
            .get("info")
            .and_then(|i| i.get("websites"))
            .and_then(|w| w.as_array())
            .map(|a| a.len() as i32)
            .unwrap_or(0),
        social_count: pair_data
            .get("info")
            .and_then(|i| i.get("socials"))
            .and_then(|s| s.as_array())
            .map(|a| a.len() as i32)
            .unwrap_or(0),
        boosts_active: pair_data
            .get("boosts")
            .and_then(|b| b.get("active"))
            .and_then(|a| a.as_i64())
            .unwrap_or(0) as i32,
    })
}

fn score_token(
    config: &Config,
    token: &crate::clients::binance_alpha::AlphaToken,
    snapshot: &PairSnapshot,
) -> super::signals::SignalDecision {
    let thresholds = FeatureThresholds {
        min_liquidity_usd: config.min_liquidity_usd,
        archive_liquidity_usd: config.archive_liquidity_usd,
        min_buy_count_m5: config.min_buy_count_m5,
        max_pair_age_hours: 24.0,
    };
    let features = build_feature_vector(&snapshot, &thresholds, "binance_alpha");

    // Evaluate signal
    let signal_config = SignalConfig {
        min_liquidity_usd: config.min_liquidity_usd,
        min_volume_h1_usd: config.min_volume_h1_usd,
        min_buy_count_m5: config.min_buy_count_m5,
        min_buy_sell_ratio_m5: config.min_buy_sell_ratio_m5,
        focus_volume_to_liquidity_ratio: config.focus_volume_to_liquidity_ratio,
        focus_score_threshold: config.focus_score_threshold,
        alert_score_threshold: config.alert_score_threshold,
        strategy_version: "v1".into(),
    };

    // Use alpha token metadata as token_metadata
    let token_metadata = serde_json::json!({
        "alpha_score": token.raw.get("score"),
        "holder_count": token.raw.get("holders"),
        "alpha_liquidity_usd": token.raw.get("liquidity"),
        "alpha_volume_h24_usd": token.raw.get("volume24h"),
        "binance_futures_listed": token.raw.get("binanceFuturesListed"),
    });

    let decision = evaluate(
        &features,
        &snapshot.observed_at,
        "binance_alpha",
        &signal_config,
        Some(&token_metadata),
    );
    decision
}

fn write_scored_pair(
    db_path: &str,
    token: &crate::clients::binance_alpha::AlphaToken,
    pair_data: &Value,
    snapshot: &PairSnapshot,
    decision: &super::signals::SignalDecision,
) -> Result<(), rusqlite::Error> {
    db::upsert_pair_and_write(
        db_path,
        &token,
        &snapshot.pair_address,
        pair_data
            .get("dexId")
            .and_then(|dex| dex.as_str())
            .unwrap_or_default(),
        snapshot,
        decision,
        &signal_v2_store::metadata(pair_data, &snapshot.observed_at),
    )?;

    Ok(())
}

#[cfg(test)]
fn process_and_write(
    db_path: &str,
    config: &Config,
    token: &crate::clients::binance_alpha::AlphaToken,
    pair_data: &Value,
) -> Result<(), rusqlite::Error> {
    let Some(snapshot) = snapshot_from_pair(token, pair_data, &chrono::Utc::now().to_rfc3339())
    else {
        return Ok(());
    };
    let decision = score_token(config, token, &snapshot);
    write_scored_pair(db_path, token, pair_data, &snapshot, &decision)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_signal_uses_combined_pool_activity() {
        let pair = |address: &str, liquidity: f64, volume: f64, buys: i32| PairSnapshot {
            pair_address: address.into(),
            token_address: "0xtoken".into(),
            observed_at: "2026-10-23T10:00:00Z".into(),
            pair_created_at: "2026-10-01T00:00:00Z".into(),
            price_usd: Some(1.0),
            price_native: None,
            liquidity_usd: Some(liquidity),
            fdv: Some(500_000.0),
            market_cap: Some(300_000.0),
            volume_m5: 2_000.0,
            volume_h1: volume,
            volume_h24: volume * 12.0,
            buys_m5: buys,
            sells_m5: 2,
            buys_h1: buys * 4,
            sells_h1: 8,
            price_change_m5: 1.0,
            price_change_h1: 2.0,
            price_change_h24: 3.0,
            website_count: 0,
            social_count: 0,
            boosts_active: 0,
        };
        let aggregate = aggregate_snapshots(&[
            pair("0xa", 20_000.0, 10_000.0, 5),
            pair("0xb", 10_000.0, 8_000.0, 5),
        ])
        .unwrap();
        assert_eq!(aggregate.liquidity_usd, Some(30_000.0));
        assert_eq!(aggregate.volume_h1, 18_000.0);
        assert_eq!(aggregate.buys_m5, 10);
        let features =
            build_feature_vector(&aggregate, &FeatureThresholds::default(), "binance_alpha");
        let decision = evaluate(
            &features,
            &aggregate.observed_at,
            "binance_alpha",
            &SignalConfig::default(),
            None,
        );
        assert!(decision.reasons.contains(&"h1_volume_support".to_string()));
        assert!(decision.reasons.contains(&"m5_buy_dominance".to_string()));
    }

    #[test]
    fn all_selected_pools_persist_one_token_decision() {
        let path = std::env::temp_dir().join(format!(
            "tmm-merged-score-{}-{}.db",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let path = path.to_str().unwrap();
        db::init_schema(path).unwrap();
        let token = crate::clients::binance_alpha::AlphaToken {
            address: "0xtoken".into(),
            symbol: "T".into(),
            name: "Token".into(),
            raw: serde_json::Value::Null,
        };
        let now = chrono::Utc::now().to_rfc3339();
        let pairs = [
            serde_json::json!({"pairAddress":"0xpan","dexId":"pancakeswap","priceUsd":"1","liquidity":{"usd":20_000.0},"fdv":500_000.0,"volume":{"h1":10_000.0}}),
            serde_json::json!({"pairAddress":"0xuni","dexId":"uniswap","priceUsd":"1","liquidity":{"usd":10_000.0},"fdv":500_000.0,"volume":{"h1":8_000.0}}),
        ];
        let snapshots: Vec<_> = pairs
            .iter()
            .map(|pair| snapshot_from_pair(&token, pair, &now).unwrap())
            .collect();
        let decision = score_token(
            &Config::from_env(),
            &token,
            &aggregate_snapshots(&snapshots).unwrap(),
        );
        assert!(decision.reasons.contains(&"h1_volume_support".into()));
        for (pair, snapshot) in pairs.iter().zip(&snapshots) {
            write_scored_pair(path, &token, pair, snapshot, &decision).unwrap();
        }
        let rows = db::overview(path, 10).unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["signal_score"], rows[1]["signal_score"]);
        assert_eq!(rows[0]["signal_reasons"], rows[1]["signal_reasons"]);
        let conn = rusqlite::Connection::open(path).unwrap();
        let mut query = conn.prepare("SELECT raw_json FROM snapshots").unwrap();
        for raw in query.query_map([], |r| r.get::<_, String>(0)).unwrap() {
            let raw: Value = serde_json::from_str(&raw.unwrap()).unwrap();
            assert_eq!(raw["signal_v2"]["presence"]["volume_h1"], true);
            assert_eq!(raw["signal_v2"]["presence"]["volume_m5"], false);
        }
        let before = db::overview(path, 10).unwrap();
        conn.execute("DROP TABLE signal_v2_changes", []).unwrap();
        // Use a valid sample so the independent transaction actually reaches its missing table.
        let sample = super::super::signal_v2::Sample {
            at: chrono::Utc::now().timestamp(),
            pair_address: "0xpan".into(),
            price: Some(1.0),
            liquidity_usd: Some(20000.0),
            volume_h1: Some(10000.0),
            volume_m5: None,
            fingerprint: "request".into(),
        };
        assert!(super::super::signal_v2_store::observe(
            path,
            &token.address,
            &[sample],
            &now,
            65,
            &[],
            &super::super::signal_v2::Settings::default()
        )
        .is_err());
        assert_eq!(db::overview(path, 10).unwrap(), before);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn context_fields_and_extreme_turnover_do_not_double_score() {
        let token = crate::clients::binance_alpha::AlphaToken {
            address: "0xtoken".into(),
            symbol: "T".into(),
            name: "T".into(),
            raw: serde_json::json!({"holders":100_000}),
        };
        let pair = serde_json::json!({"pairAddress":"0xpair","priceUsd":"1","liquidity":{"usd":20_000.0},"fdv":200_000.0,"volume":{"h1":100_000.0},"info":{"websites":[{}],"socials":[{}]},"boosts":{"active":1}});
        let snapshot = snapshot_from_pair(&token, &pair, &chrono::Utc::now().to_rfc3339()).unwrap();
        let decision = score_token(&Config::from_env(), &token, &snapshot);
        for retired in [
            "project_metadata_present",
            "partial_metadata_present",
            "active_dex_boost",
            "holder_depth",
            "speculative_pool_activity",
        ] {
            assert!(
                !decision.reasons.contains(&retired.to_string()),
                "{retired}"
            );
        }
        let mut larger = snapshot.clone();
        larger.liquidity_usd = Some(260_000.0);
        let smaller_features =
            build_feature_vector(&snapshot, &FeatureThresholds::default(), "binance_alpha");
        let larger_features =
            build_feature_vector(&larger, &FeatureThresholds::default(), "binance_alpha");
        assert!(evaluate(
            &larger_features,
            &snapshot.observed_at,
            "binance_alpha",
            &SignalConfig::default(),
            None
        )
        .reasons
        .contains(&"ample_liquidity".to_string()));
        assert!(evaluate(
            &smaller_features,
            &snapshot.observed_at,
            "binance_alpha",
            &SignalConfig::default(),
            None
        )
        .reasons
        .contains(&"healthy_liquidity_band".to_string()));
    }

    #[test]
    fn alpha_scan_continues_after_last_token_and_wraps() {
        let tokens: Vec<_> = ["a", "b", "c"]
            .into_iter()
            .map(|address| crate::clients::binance_alpha::AlphaToken {
                address: address.into(),
                symbol: address.into(),
                name: String::new(),
                raw: Value::Null,
            })
            .collect();
        assert_eq!(scan_start(&tokens, None), 0);
        assert_eq!(scan_start(&tokens, Some("a")), 1);
        assert_eq!(scan_start(&tokens, Some("c")), 0);
        assert_eq!(scan_start(&tokens, Some("removed")), 0);
    }

    #[test]
    fn failed_token_retry_does_not_replace_the_rest_of_the_batch() {
        let tokens: Vec<_> = ["a", "b", "c"]
            .into_iter()
            .map(|address| crate::clients::binance_alpha::AlphaToken {
                address: address.into(),
                symbol: address.into(),
                name: String::new(),
                raw: Value::Null,
            })
            .collect();
        let mut failures = std::collections::HashMap::new();
        failures.insert(
            "b".to_string(),
            (1, std::time::Instant::now() - Duration::from_secs(1)),
        );
        assert_eq!(
            scan_work(&tokens, 2, 3, &failures, &[]),
            vec![(1, true), (2, false), (0, false), (1, false)]
        );
        assert_eq!(dex_retry_delay(1), Duration::from_secs(30));
        assert_eq!(dex_retry_delay(9), Duration::from_secs(300));
    }

    #[test]
    fn short_cycle_priority_precedes_normal_scan_without_losing_cursor_progress() {
        let tokens: Vec<_> = ["a", "b", "c"]
            .into_iter()
            .map(|address| crate::clients::binance_alpha::AlphaToken {
                address: address.into(),
                symbol: address.into(),
                name: String::new(),
                raw: Value::Null,
            })
            .collect();
        assert_eq!(
            scan_work(
                &tokens,
                0,
                2,
                &std::collections::HashMap::new(),
                &["c".into()]
            ),
            vec![(2, false), (0, false)]
        );
    }

    #[test]
    fn saturated_priority_batch_still_advances_normal_scan() {
        let tokens: Vec<_> = ["a", "b", "c", "d", "e", "f", "g", "h"]
            .into_iter()
            .map(|address| crate::clients::binance_alpha::AlphaToken {
                address: address.into(),
                symbol: address.into(),
                name: String::new(),
                raw: Value::Null,
            })
            .collect();
        let priority = ["h", "g", "f", "e", "d", "c", "b", "a"].map(str::to_string);
        assert_eq!(
            scan_work(&tokens, 0, 8, &std::collections::HashMap::new(), &priority),
            vec![
                (7, false),
                (6, false),
                (5, false),
                (4, false),
                (3, false),
                (2, false),
                (0, false),
                (1, false)
            ]
        );
    }

    #[test]
    fn displayed_tokens_after_the_first_fifty_keep_five_minute_priority() {
        let visible: Vec<String> = (0..51).map(|i| format!("token{i}")).collect();
        let hot = visible[..50].to_vec();
        assert_eq!(market_refresh_age("token0", &[], &hot, &visible), 20);
        assert_eq!(market_refresh_age("token50", &[], &hot, &visible), 30);
        assert_eq!(market_refresh_age("hidden", &[], &hot, &visible), 90);
        assert_eq!(
            market_refresh_age("hidden", &["hidden".into()], &hot, &visible),
            20
        );
    }

    #[test]
    fn futures_listed_tokens_filters_by_usdt_symbol() {
        let tokens = vec![
            crate::clients::binance_alpha::AlphaToken {
                address: "listed".into(),
                symbol: "ip".into(),
                name: "Listed".into(),
                raw: serde_json::Value::Null,
            },
            crate::clients::binance_alpha::AlphaToken {
                address: "unlisted".into(),
                symbol: "no-fut".into(),
                name: "Unlisted".into(),
                raw: serde_json::Value::Null,
            },
        ];
        let symbols = std::collections::HashSet::from(["IPUSDT".to_string()]);
        let filtered = super::futures_listed_tokens(tokens, &symbols);
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered[0].address, "listed");
    }

    #[test]
    fn selects_several_pools_per_supported_dex_without_unbounded_fanout() {
        let pairs: Vec<Value> = [
            "pancakeswap",
            "pancakeswap",
            "pancakeswap",
            "pancakeswap",
            "uniswap",
            "other",
        ]
        .into_iter()
        .enumerate()
        .map(|(i, dex)| serde_json::json!({"dexId":dex,"pairAddress":format!("pool{i}")}))
        .collect();
        let selected = selected_pools(&pairs);
        let addresses: Vec<_> = selected
            .iter()
            .map(|p| p["pairAddress"].as_str().unwrap())
            .collect();
        assert_eq!(addresses, ["pool0", "pool1", "pool2", "pool4"]);
    }

    #[test]
    fn alpha_activity_keeps_partial_pool_coverage_visible() {
        let path = std::env::temp_dir().join(format!("tmm-alpha-score-{}.db", std::process::id()));
        let path = path.to_str().unwrap();
        db::init_schema(path).unwrap();
        let token = crate::clients::binance_alpha::AlphaToken {
            address: "0xjct".into(),
            symbol: "JCT".into(),
            name: "Janction".into(),
            raw: serde_json::json!({"score":101,"holders":"15366","liquidity":"889153","volume24h":"13192451"}),
        };
        let pair = serde_json::json!({
            "pairAddress":"0xjctpair","dexId":"uniswap","priceUsd":"0.0043",
            "liquidity":{"usd":7000.0},"fdv":44460000.0,
            "volume":{"h1":7057.0,"m5":250.0},
            "info":{"websites":[{}],"socials":[{}]}
        });
        let mut config = Config::from_env();
        config.min_liquidity_usd = 15_000.0;
        config.archive_liquidity_usd = 3_000.0;
        config.min_volume_h1_usd = 15_000.0;
        process_and_write(path, &config, &token, &pair).unwrap();
        let row = db::overview(path, 1).unwrap().remove(0);
        assert_eq!(
            row["liquidity_usd"], 7000.0,
            "pool liquidity must keep its original source"
        );
        assert!(row["signal_score"].as_i64().unwrap() > 0);
        assert!(row["signal_reasons"]
            .as_str()
            .unwrap()
            .contains("alpha_activity_support"));
        let mut quiet_token = token.clone();
        quiet_token.address = "0xquiet".into();
        quiet_token.raw = serde_json::json!({"liquidity":"889153","volume24h":"0"});
        let mut quiet_pair = pair.clone();
        quiet_pair["pairAddress"] = "0xquietpair".into();
        process_and_write(path, &config, &quiet_token, &quiet_pair).unwrap();
        let quiet = db::overview(path, 10)
            .unwrap()
            .into_iter()
            .find(|row| row["token_address"] == "0xquiet")
            .unwrap();
        assert_eq!(
            quiet["signal_score"], 0,
            "Alpha liquidity alone must not rescue a quiet token"
        );
        assert!(db::token_snapshot_recent(path, &token.address, 300).unwrap());
        let conn = rusqlite::Connection::open(path).unwrap();
        let old = (chrono::Utc::now() - chrono::Duration::minutes(6)).to_rfc3339();
        conn.execute(
            "UPDATE pairs SET last_snapshot_at = ?1 WHERE token_address = ?2",
            rusqlite::params![old, token.address],
        )
        .unwrap();
        assert!(!db::token_snapshot_recent(path, &token.address, 300).unwrap());
        drop(conn);
        std::fs::remove_file(path).unwrap();
    }
}
