//! Read-only replay; forward observations never enter detection history early.
use super::signal_v2::{self, Sample, Settings, State};
use super::signal_v2_store::{self, EXPERIMENT_VERSION};
use chrono::{DateTime, Duration, Utc};
use rusqlite::{Connection, OpenFlags};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
type Counts = BTreeMap<String, u64>;
fn count(counts: &mut Counts, key: &str) {
    *counts.entry(key.into()).or_default() += 1;
}
fn counters(keys: &[&str]) -> Counts {
    keys.iter().map(|key| ((*key).into(), 0)).collect()
}
const HORIZONS: [(&str, i64); 3] = [("15m", 900), ("1h", 3600), ("4h", 14400)];
const PATH_HORIZON_SECONDS: i64 = 14_400;
const PATH_GRACE_SECONDS: i64 = 300;
const PATH_TARGET_PCT: f64 = 10.0;
const PATH_STOP_PCT: f64 = -10.0;
const PATH_ASSUMED_ROUND_TRIP_COST_PCT: f64 = 0.0;
const EXIT_COST_SENSITIVITY_PCT: [f64; 4] = [0.0, 0.3, 0.5, 1.0];
const EXIT_STRATEGY_NAMES: [&str; 3] = [
    "fixed_take_profit",
    "no_fixed_take_profit",
    "ladder_take_profit",
];
const A_ENTRY_DELAYS_SECONDS: [i64; 5] = [60, 300, 900, 1800, 3600];
const B_COHORTS: [&str; 9] = [
    "all",
    "pass",
    "pass_recent_volume",
    "pass_near_breakout",
    "pass_both",
    "pass_distance_0_3pct",
    "pass_distance_3_5pct",
    "pass_distance_5_8pct",
    "pass_distance_gt8pct",
];

#[derive(Default, Serialize)]
struct Outcome {
    denominator: u64,
    eligible: u64,
    endpoint_missing: u64,
    right_censored: u64,
    positive: u64,
    negative: u64,
    flat: u64,
    positive_sampled_return_rate_pct: Option<f64>,
    mean_sampled_return_pct: Option<f64>,
    min_sampled_return_pct: Option<f64>,
    max_sampled_return_pct: Option<f64>,
}
impl Outcome {
    fn record(&mut self, value: f64) {
        self.eligible += 1;
        if value > 0.0 {
            self.positive += 1;
        } else if value < 0.0 {
            self.negative += 1;
        } else {
            self.flat += 1;
        }
        self.positive_sampled_return_rate_pct =
            Some(self.positive as f64 / self.eligible as f64 * 100.0);
        let mean = self.mean_sampled_return_pct.unwrap_or(0.0);
        self.mean_sampled_return_pct = Some(mean + (value - mean) / self.eligible as f64);
        self.min_sampled_return_pct = Some(self.min_sampled_return_pct.unwrap_or(value).min(value));
        self.max_sampled_return_pct = Some(self.max_sampled_return_pct.unwrap_or(value).max(value));
    }
}

#[derive(Default, Serialize)]
struct PathOutcome {
    denominator: u64,
    resolved: u64,
    target_first: u64,
    stop_first: u64,
    timeout: u64,
    unresolved: u64,
    endpoint_missing: u64,
    wins: u64,
    losses: u64,
    mean_gross_return_pct: Option<f64>,
    mean_net_return_pct: Option<f64>,
    total_net_profit_pct: f64,
    total_net_loss_pct: f64,
    profit_factor: Option<f64>,
}
impl PathOutcome {
    fn record_exit(&mut self, kind: &'static str, gross_return_pct: f64) {
        self.resolved += 1;
        match kind {
            "target_first" => self.target_first += 1,
            "stop_first" => self.stop_first += 1,
            _ => self.timeout += 1,
        }
        let net_return_pct = gross_return_pct - PATH_ASSUMED_ROUND_TRIP_COST_PCT;
        if net_return_pct > 0.0 {
            self.wins += 1;
        } else if net_return_pct < 0.0 {
            self.losses += 1;
        }
        if net_return_pct > 0.0 {
            self.total_net_profit_pct += net_return_pct;
        } else if net_return_pct < 0.0 {
            self.total_net_loss_pct += -net_return_pct;
        }
        let mean_gross = self.mean_gross_return_pct.unwrap_or(0.0);
        self.mean_gross_return_pct =
            Some(mean_gross + (gross_return_pct - mean_gross) / self.resolved as f64);
        let mean_net = self.mean_net_return_pct.unwrap_or(0.0);
        self.mean_net_return_pct =
            Some(mean_net + (net_return_pct - mean_net) / self.resolved as f64);
        self.profit_factor = if self.total_net_loss_pct > 0.0 {
            Some(self.total_net_profit_pct / self.total_net_loss_pct)
        } else {
            None
        };
    }
}

#[derive(Default, Serialize)]
struct ExitCostOutcome {
    cost_pct: f64,
    denominator: u64,
    resolved: u64,
    unresolved: u64,
    endpoint_missing: u64,
    wins: u64,
    losses: u64,
    mean_net_return_pct: Option<f64>,
    total_net_profit_pct: f64,
    total_net_loss_pct: f64,
    profit_factor: Option<f64>,
}

impl ExitCostOutcome {
    fn record_exit(&mut self, gross_return_pct: f64) {
        self.resolved += 1;
        let net_return_pct = gross_return_pct - self.cost_pct;
        if net_return_pct > 0.0 {
            self.wins += 1;
            self.total_net_profit_pct += net_return_pct;
        } else if net_return_pct < 0.0 {
            self.losses += 1;
            self.total_net_loss_pct += -net_return_pct;
        }
        let mean = self.mean_net_return_pct.unwrap_or(0.0);
        self.mean_net_return_pct = Some(mean + (net_return_pct - mean) / self.resolved as f64);
        self.profit_factor = (self.total_net_loss_pct > 0.0)
            .then_some(self.total_net_profit_pct / self.total_net_loss_pct);
    }
}

#[derive(Default, Serialize)]
struct ExitStrategyOutcome {
    denominator: u64,
    resolved: u64,
    target_first: u64,
    stop: u64,
    timeout: u64,
    first_target: u64,
    second_target: u64,
    trailing_stop: u64,
    unresolved: u64,
    endpoint_missing: u64,
    wins: u64,
    losses: u64,
    mean_gross_return_pct: Option<f64>,
    mean_net_return_pct: Option<f64>,
    total_net_profit_pct: f64,
    total_net_loss_pct: f64,
    profit_factor: Option<f64>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    cost_sensitivity: Vec<ExitCostOutcome>,
}
impl ExitStrategyOutcome {
    fn record_milestone(&mut self, kind: &str) {
        match kind {
            "first_target" => self.first_target += 1,
            "second_target" => self.second_target += 1,
            _ => {}
        }
    }
    fn record_exit(&mut self, kind: &str, gross_return_pct: f64) {
        self.resolved += 1;
        match kind {
            "target_first" => self.target_first += 1,
            "stop" | "stop_first" => self.stop += 1,
            "trailing_stop" => self.trailing_stop += 1,
            _ => self.timeout += 1,
        }
        let net_return_pct = gross_return_pct - PATH_ASSUMED_ROUND_TRIP_COST_PCT;
        if net_return_pct > 0.0 {
            self.wins += 1;
            self.total_net_profit_pct += net_return_pct;
        } else if net_return_pct < 0.0 {
            self.losses += 1;
            self.total_net_loss_pct += -net_return_pct;
        }
        let mean_gross = self.mean_gross_return_pct.unwrap_or(0.0);
        self.mean_gross_return_pct =
            Some(mean_gross + (gross_return_pct - mean_gross) / self.resolved as f64);
        let mean_net = self.mean_net_return_pct.unwrap_or(0.0);
        self.mean_net_return_pct =
            Some(mean_net + (net_return_pct - mean_net) / self.resolved as f64);
        self.profit_factor = (self.total_net_loss_pct > 0.0)
            .then_some(self.total_net_profit_pct / self.total_net_loss_pct);
        for outcome in &mut self.cost_sensitivity {
            outcome.record_exit(gross_return_pct);
        }
    }

    fn record_denominator(&mut self) {
        if self.cost_sensitivity.is_empty() {
            self.cost_sensitivity = EXIT_COST_SENSITIVITY_PCT
                .into_iter()
                .map(|cost_pct| ExitCostOutcome {
                    cost_pct,
                    ..ExitCostOutcome::default()
                })
                .collect();
        }
        for outcome in &mut self.cost_sensitivity {
            outcome.denominator += 1;
        }
    }

    fn record_incomplete(&mut self, unresolved: bool) {
        for outcome in &mut self.cost_sensitivity {
            if unresolved {
                outcome.unresolved += 1;
            } else {
                outcome.endpoint_missing += 1;
            }
        }
    }
}
#[derive(Serialize)]
struct Hits {
    events: u64,
    first_at: Option<i64>,
    last_at: Option<i64>,
    risk_status: Counts,
    data_status: Counts,
    forward: BTreeMap<String, Outcome>,
    path: PathOutcome,
    exit_strategies: BTreeMap<String, ExitStrategyOutcome>,
}
impl Default for Hits {
    fn default() -> Self {
        Self {
            events: 0,
            first_at: None,
            last_at: None,
            risk_status: Counts::new(),
            data_status: Counts::new(),
            forward: HORIZONS
                .iter()
                .map(|(name, _)| ((*name).into(), Outcome::default()))
                .collect(),
            path: PathOutcome::default(),
            exit_strategies: EXIT_STRATEGY_NAMES
                .into_iter()
                .map(|name| (name.into(), ExitStrategyOutcome::default()))
                .collect(),
        }
    }
}
struct Row {
    at: DateTime<Utc>,
    pair: String,
    group: String,
    sample: Option<Sample>,
    score: Option<i32>,
    risks: Option<Vec<String>>,
}
struct Pending {
    keys: Vec<String>,
    token: String,
    at: DateTime<Utc>,
    pair: String,
    price: f64,
    previous: (DateTime<Utc>, f64),
    remaining: Vec<(&'static str, i64)>,
    path_exit: Option<(&'static str, f64)>,
    strategy_exits: BTreeMap<String, Option<(&'static str, f64)>>,
    ladder_high_pct: f64,
    ladder_first: bool,
    ladder_second: bool,
}

struct DelayedEntry {
    key: String,
    token: String,
    pair: String,
    target: DateTime<Utc>,
}

#[derive(Clone, Serialize)]
struct PortfolioEvent {
    token: String,
    entry_at: i64,
    exit_at: i64,
    gross_return_pct: f64,
}

#[derive(Default, Serialize)]
struct PortfolioPolicy {
    opened: u64,
    skipped_capacity: u64,
    skipped_cooldown: u64,
    wins: u64,
    losses: u64,
    final_equity: f64,
    max_drawdown_pct: f64,
    exposure_pct: f64,
}

fn simulate_portfolio(events: &[PortfolioEvent]) -> BTreeMap<String, PortfolioPolicy> {
    let mut policies = BTreeMap::new();
    let Some(first_entry) = events.iter().map(|event| event.entry_at).min() else {
        return policies;
    };
    let Some(last_exit) = events
        .iter()
        .map(|event| event.exit_at.max(event.entry_at))
        .max()
    else {
        return policies;
    };
    let window_seconds = (last_exit - first_entry).max(1) as f64;
    for max_positions in [1usize, 2, 3, 5] {
        let mut sorted = events.to_vec();
        sorted.sort_by_key(|event| (event.entry_at, event.token.clone()));
        let mut active_exits: Vec<i64> = Vec::new();
        let mut cooldown_until: BTreeMap<String, i64> = BTreeMap::new();
        let mut opened = Vec::new();
        let mut policy = PortfolioPolicy::default();
        for event in sorted {
            active_exits.retain(|exit_at| *exit_at > event.entry_at);
            if cooldown_until
                .get(&event.token)
                .is_some_and(|until| event.entry_at < *until)
            {
                policy.skipped_cooldown += 1;
                continue;
            }
            if active_exits.len() >= max_positions {
                policy.skipped_capacity += 1;
                continue;
            }
            active_exits.push(event.exit_at);
            cooldown_until.insert(event.token.clone(), event.entry_at + 3600);
            opened.push(event);
        }
        policy.opened = opened.len() as u64;
        opened.sort_by_key(|event| event.exit_at);
        let mut equity = 1.0;
        let mut peak = 1.0_f64;
        let mut invested_seconds = 0_i64;
        for event in &opened {
            let net_return = (event.gross_return_pct - 0.3) / 100.0 / max_positions as f64;
            equity *= 1.0 + net_return;
            peak = peak.max(equity);
            policy.max_drawdown_pct = policy.max_drawdown_pct.max((peak - equity) / peak * 100.0);
            if net_return > 0.0 {
                policy.wins += 1;
            } else if net_return < 0.0 {
                policy.losses += 1;
            }
            invested_seconds += event.exit_at.saturating_sub(event.entry_at).max(1);
        }
        policy.final_equity = equity;
        policy.exposure_pct =
            invested_seconds as f64 / (window_seconds * max_positions as f64) * 100.0;
        policies.insert(format!("max_positions_{max_positions}"), policy);
    }
    policies
}

/// Opens the source read-only before any application startup, migration, or persistence.
pub fn run(path: &str, args: &[String], settings: &Settings) -> Result<Value> {
    let proxy = match args {
        [] => false,
        [arg] if arg == "--legacy-proxy" => true,
        _ => return Err("usage: signal-v2-replay [--legacy-proxy]".into()),
    };
    if !signal_v2_store::settings_valid(settings) || settings.focus_score_threshold < 1 {
        return Err("invalid shadow settings".into());
    }
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    conn.busy_timeout(std::time::Duration::from_millis(100))?;
    // Keep the two input scans in one consistent SQLite read snapshot.
    conn.execute_batch("BEGIN")?;
    let mut input = counters(&[
        "total_rows",
        "window_rows",
        "older_rows",
        "invalid_time_rows",
        "accepted_rows",
        "strict_rows",
        "proxy_rows",
        "excluded_rows",
        "legacy_rows",
        "groups",
        "tokens",
        "groups_over_three_pools",
    ]);
    let mut coverage = counters(&[
        "frozen_tokens",
        "reference_missing_groups",
        "invalid_reference_groups",
        "no_eligible_reference_groups",
        "frozen_pool_rows",
        "accepted_evidence_groups",
        "duplicate_or_unchanged_groups",
        "accepted_evidence_intervals",
        "accepted_evidence_gaps_over_5m",
        "reference_intervals",
        "reference_intervals_over_5m",
        "missing_price_rows",
        "missing_h1_rows",
        "missing_m5_rows",
        "missing_liquidity_rows",
        "warming_up_groups",
        "signal_missing_groups",
        "signal_conflict_groups",
        "history_limit_groups",
        "reference_switches",
    ]);
    let mut hits: BTreeMap<String, Hits> = ["A", "B", "C"]
        .into_iter()
        .map(|stage| (stage.into(), Hits::default()))
        .collect();
    for name in B_COHORTS {
        hits.insert(format!("B/{name}"), Hits::default());
    }
    let mut first: Option<DateTime<Utc>> = None;
    let mut last: Option<DateTime<Utc>> = None;
    let mut times = conn.prepare("SELECT observed_at,julianday(observed_at) FROM snapshots")?;
    for at in times.query_map([], |r| {
        Ok((r.get::<_, String>(0)?, r.get::<_, Option<f64>>(1)?))
    })? {
        count(&mut input, "total_rows");
        let (text, sql_time) = at?;
        match DateTime::parse_from_rfc3339(&text) {
            Ok(at) if sql_time.is_some() => {
                let at = at.with_timezone(&Utc);
                first = Some(first.unwrap_or(at).min(at));
                last = Some(last.unwrap_or(at).max(at));
            }
            _ => count(&mut input, "invalid_time_rows"),
        }
    }
    let from = last.map(|end| first.unwrap().max(end - Duration::days(7)));
    let mut query = conn.prepare("SELECT s.token_address,s.pair_address,s.observed_at,s.price_usd,s.volume_h1,s.volume_m5,s.liquidity_usd,s.raw_json,g.score,g.risk_flags,julianday(s.observed_at)
        FROM snapshots s LEFT JOIN signals g ON g.id=(SELECT id FROM signals WHERE pair_address=s.pair_address AND token_address=s.token_address AND observed_at=s.observed_at ORDER BY id DESC LIMIT 1)
        ORDER BY s.token_address,julianday(s.observed_at),s.id")?;
    let mut cursor = query.query([])?;
    let mut token = String::new();
    let mut rows = Vec::new();
    let mut bucket_time = None;
    let mut portfolio_events = Vec::new();
    let mut replay = TokenReplay {
        from: from.map(|time| time.timestamp()).unwrap_or(0),
        ..TokenReplay::default()
    };
    while let Some(row) = cursor.next()? {
        let next_token: String = row.get(0)?;
        let text: String = row.get(2)?;
        let Ok(parsed) = DateTime::parse_from_rfc3339(&text) else {
            continue;
        };
        let at = parsed.with_timezone(&Utc);
        let Some(sql_time) = row.get::<_, Option<f64>>(10)? else {
            continue;
        };
        if at < from.unwrap() {
            count(&mut input, "older_rows");
            continue;
        }
        if !rows.is_empty() && (next_token != token || bucket_time != Some(sql_time)) {
            flush(
                &conn,
                &token,
                &mut rows,
                &mut replay,
                proxy,
                settings,
                &mut input,
                &mut coverage,
                &mut hits,
            )?;
        }
        if next_token != token {
            replay.finish(last.unwrap(), &mut hits);
            portfolio_events.extend(replay.portfolio_events.iter().cloned());
            replay = TokenReplay {
                from: from.unwrap().timestamp(),
                ..TokenReplay::default()
            };
            count(&mut input, "tokens");
        }
        token = next_token;
        bucket_time = Some(sql_time);
        // shortcut: 4096 rows per millisecond bound tie sorting; fail rather than truncate denser inputs.
        if rows.len() >= 4096 {
            return Err("replay response sort budget exceeded".into());
        }
        count(&mut input, "window_rows");
        let pair: String = row.get(1)?;
        let values: [Option<f64>; 4] = [row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?];
        let raw: String = row.get(7)?;
        let raw: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
        let legacy = raw == json!({});
        let is_proxy = proxy && legacy;
        let sample = decode(&pair, &text, values, &raw, proxy);
        if legacy {
            count(&mut input, "legacy_rows");
        }
        if let Some(sample) = &sample {
            count(&mut input, "accepted_rows");
            count(
                &mut input,
                if is_proxy {
                    "proxy_rows"
                } else {
                    "strict_rows"
                },
            );
            for (field, value) in [
                ("missing_price_rows", sample.price),
                ("missing_h1_rows", sample.volume_h1),
                ("missing_m5_rows", sample.volume_m5),
                ("missing_liquidity_rows", sample.liquidity_usd),
            ] {
                if value.is_none() {
                    count(&mut coverage, field);
                }
            }
        } else {
            count(&mut input, "excluded_rows");
        }
        let group = sample
            .as_ref()
            .map(|s| s.fingerprint.clone())
            .or_else(|| raw["signal_v2"]["request_id"].as_str().map(str::to_string))
            .unwrap_or_else(|| format!("legacy:{text}"));
        let risks: Option<String> = row.get(9)?;
        rows.push(Row {
            at,
            pair,
            group,
            sample,
            score: row.get(8).ok().flatten(),
            risks: risks.and_then(|v| serde_json::from_str(&v).ok()),
        });
    }
    flush(
        &conn,
        &token,
        &mut rows,
        &mut replay,
        proxy,
        settings,
        &mut input,
        &mut coverage,
        &mut hits,
    )?;
    replay.finish(last.unwrap_or_else(Utc::now), &mut hits);
    portfolio_events.extend(replay.portfolio_events.iter().cloned());
    let portfolio_policies = simulate_portfolio(&portfolio_events);
    let mut cohorts = BTreeMap::new();
    let mut by_date: BTreeMap<String, BTreeMap<String, Hits>> = BTreeMap::new();
    let keys: Vec<_> = hits
        .keys()
        .filter(|key| key.starts_with("B/"))
        .cloned()
        .collect();
    for key in keys {
        let hit = hits.remove(&key).unwrap();
        if let Some(date_key) = key.strip_prefix("B/date/") {
            let (date, name) = date_key.split_once('/').unwrap();
            by_date
                .entry(date.into())
                .or_insert_with(|| {
                    B_COHORTS
                        .into_iter()
                        .map(|name| (name.into(), Hits::default()))
                        .collect()
                })
                .insert(name.into(), hit);
        } else {
            cohorts.insert(key[2..].to_string(), hit);
        }
    }
    let recent_evidence: Counts = ["pass", "fail", "unknown"]
        .into_iter()
        .map(|name| {
            (
                name.into(),
                coverage
                    .get(&format!("b_recent_volume:{name}"))
                    .copied()
                    .unwrap_or(0),
            )
        })
        .collect();
    Ok(json!({
        "mode":if proxy {"legacy_proxy_exploratory"} else {"strict"},
        "experiment_identity":{"experiment_version":EXPERIMENT_VERSION,"settings":settings},
        "source_time_unverified":true,"opportunity_eligible_events":0,
        "frame_completeness_verified":false,"runtime_execution_trace_reproduced":false,
        "window":{"available_from":first,"available_until":last,"replay_from":from,"replay_until":last,"max_days":7},
        "input":input,"coverage":coverage,"structural_hits":hits,
        "portfolio":{"events":portfolio_events.len(),"policies":portfolio_policies},
        "b_filter_comparison":{
            "experiment_version":"b_quality_v1",
            "cohorts":cohorts,"by_utc_date":by_date,"recent_volume_evidence":recent_evidence,
            "methods":{
                "baseline":"all includes every structural B; pass and all filtered/distance cohorts require contemporaneous risk PASS; all remain source-time-unverified and opportunity-ineligible",
                "recent_volume":"current m5 >= current h1/12 and >= 0.8 * positive crossing m5; current m5/h1 must be positive; prior same-pool samples at b_crossed_at must all agree on known positive crossing m5; otherwise unknown",
                "near_breakout":"confirmation price <= frozen reference_upper * 1.005 * 1.03; distance buckets have inclusive upper boundaries 3/5/8 percent",
                "membership":"fixed at original B confirmation using then-known inputs; one event shares unchanged forward labels across matching cohorts; no engine changes or re-confirmation",
                "chronology":"UTC event-date cohorts; supports chronological comparison, not a certified untouched holdout or statistical significance",
                "outcomes":"positive/negative/flat count eligible sampled endpoints only; positive rate = positive / eligible, missing and censored are separate; not trade win rate or net return",
                "calibration":"fixed exploratory constants 1/12, 80%, 3%; no parameter search or automatic production promotion"
            }
        },
        "methods":{
            "ordering":"stream by token and SQLite millisecond time, exact RFC3339 sort within ties; independent stored request groups; engine clock is Unix seconds",
            "a_entry_filters":"A/pass requires contemporaneous risk PASS; pass_recent_volume additionally requires positive current m5 >= current h1/12; pass_near_breakout uses the then-known frozen line when present; cohorts without sufficient trigger evidence remain absent rather than guessed",
            "entry_timing":"A/delay_60s, A/delay_300s, A/delay_900s, A/delay_1800s, and A/delay_3600s start at the first valid future observation in [A+delay,A+delay+300s], using that observation's price; no delayed event is invented when coverage is missing",
            "reference":"first eligible stored response group, highest liquidity then lowest pool address, frozen prospectively",
            "history":"only prior positive-price reference-pool samples within the replay window and 24h, 4096 raw observations maximum; overflow cancels evidence",
            "legacy_grouping":"same token and exact observed_at proxy request identity; only empty metadata eligible for proxy fallback",
            "legacy_comparison":"exact contemporaneous stored signals, one token decision per response; unknown historical configurations",
            "forward":"same frozen pool; first positive sampled price in [trigger+horizon,trigger+horizon+300s], previous sample at/before target and bracketing gap <=300s; otherwise unavailable",
            "forward_denominator":"all structural stage events, split into eligible, endpoint_missing, right_censored; right censor if replay ends before target+300s without a usable endpoint",
            "returns":"sampled endpoint price change only; no execution, profit, fees, slippage, or path/extrema labels",
            "path":"observed snapshot path after each structural event; first observed +10% or -10% decides target/stop within 4h+300s, otherwise the first later observation exits at timeout; a single jumped observation is assigned to its observed side and cannot prove intraperiod ordering",
            "profit":"target and stop gross returns are fixed at +10%/-10%; timeout uses sampled price change; net return currently deducts no fee or slippage; no venue fill, actual fee, slippage, or position size is claimed",
            "exit_strategies":"fixed_take_profit exits fully at +10%; no_fixed_take_profit holds until -10% stop or timeout; ladder_take_profit sells 30% at +10%, 30% at +25%, then exits the remainder at the greater of +8% or a 20% drawdown from the observed high, with timeout as fallback",
        },
        "limitations":["all upstream source times unverified", "metadata verifies individual recorded rows, not response completeness; partial-write survivors may be replayed", "collected-input replay does not reproduce runtime skips from retirement failure, shadow mode/cooldown, observe/save failure or subsequent recovery", "historical request failures and original selected-pool sets unavailable for legacy rows", "proxy numeric presence and response identity unverified; zero legacy volumes unknown", "risk and score use stored contemporaneous evidence only; missing/conflicting evidence holds", "no current pairs or active-universe selection; four case identities remain unverified", "endpoint sampling does not establish continuous tradeable price coverage"]
    }))
}

fn decode(
    pair: &str,
    text: &str,
    values: [Option<f64>; 4],
    raw: &Value,
    proxy: bool,
) -> Option<Sample> {
    signal_v2_store::decode(pair, text, values, raw).or_else(|| {
        if !proxy
            || *raw != json!({})
            || pair.is_empty()
            || values.iter().flatten().any(|v| !v.is_finite() || *v < 0.0)
        {
            return None;
        }
        Some(Sample {
            at: DateTime::parse_from_rfc3339(text).ok()?.timestamp(),
            pair_address: pair.into(),
            price: values[0],
            // Legacy fallback zeros cannot establish actual zero volume.
            volume_h1: values[1].filter(|v| *v > 0.0),
            volume_m5: values[2].filter(|v| *v > 0.0),
            liquidity_usd: values[3],
            fingerprint: format!("legacy:{text}"),
        })
    })
}

fn cancel(state: &mut State, at: i64, settings: &Settings) {
    let marker = Sample {
        at,
        pair_address: String::new(),
        price: None,
        volume_h1: None,
        volume_m5: None,
        liquidity_usd: None,
        fingerprint: String::new(),
    };
    signal_v2::advance(state, &marker, &[], None, &[], settings);
}

fn initial_history(
    conn: &Connection,
    token: &str,
    pair: &str,
    at: i64,
    proxy: bool,
    window_from: i64,
) -> Result<(VecDeque<Sample>, VecDeque<i64>)> {
    let from = DateTime::from_timestamp(at.saturating_sub(86400).max(window_from), 0)
        .ok_or("invalid history time")?
        .to_rfc3339();
    let until = DateTime::from_timestamp(at, 0)
        .ok_or("invalid history time")?
        .to_rfc3339();
    let mut query=conn.prepare("SELECT observed_at,price_usd,volume_h1,volume_m5,liquidity_usd,raw_json FROM snapshots WHERE pair_address=?1 AND token_address=?2 AND observed_at>=?3 AND observed_at<?4 ORDER BY observed_at DESC LIMIT 4097")?;
    let mut cursor = query.query(rusqlite::params![pair, token, from, until])?;
    let mut samples = Vec::new();
    let mut count = 0;
    let mut times = Vec::new();
    while let Some(row) = cursor.next()? {
        count += 1;
        if count > 4096 {
            return Err("shadow history limit exceeded".into());
        }
        let text: String = row.get(0)?;
        let parsed = DateTime::parse_from_rfc3339(&text)?.timestamp();
        if parsed < window_from || parsed >= at {
            continue;
        }
        times.push(parsed);
        let raw: String = row.get(5)?;
        let raw: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
        if let Some(sample) = decode(
            pair,
            &text,
            [row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?],
            &raw,
            proxy,
        )
        .filter(|s| s.price.is_some_and(|p| p > 0.0) && s.at < at)
        {
            samples.push(sample);
        }
    }
    samples.sort_by_key(|s| s.at);
    times.sort_unstable();
    Ok((samples.into(), times.into()))
}

fn crossing_m5(conn: &Connection, token: &str, state: &State, proxy: bool) -> Result<Option<f64>> {
    let Some(crossed_at) = state.b_crossed_at else {
        return Ok(None);
    };
    let time = DateTime::from_timestamp(crossed_at, 0)
        .ok_or("invalid crossing time")?
        .to_rfc3339();
    // Price history omits invalid rows; raw crossing evidence must include them.
    // shortcut: scan one retained pool via its pair index; normalize/index time if replay event volume makes this costly.
    let mut query = conn.prepare("SELECT observed_at,price_usd,volume_h1,volume_m5,liquidity_usd,raw_json FROM snapshots WHERE pair_address=?1 AND token_address=?2 AND julianday(observed_at) BETWEEN julianday(?3)-1.0/86400 AND julianday(?3)+2.0/86400 LIMIT 4097")?;
    let mut rows = query.query(rusqlite::params![state.pair_address, token, time])?;
    let mut baseline = None;
    let mut count = 0;
    while let Some(row) = rows.next()? {
        count += 1;
        if count > 4096 {
            return Ok(None);
        }
        let text: String = row.get(0)?;
        if DateTime::parse_from_rfc3339(&text)?.timestamp() != crossed_at {
            continue;
        }
        let raw: String = row.get(5)?;
        let raw: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
        let volume = decode(
            &state.pair_address,
            &text,
            [row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?],
            &raw,
            proxy,
        )
        .and_then(|s| s.volume_m5)
        .filter(|v| *v > 0.0);
        let Some(volume) = volume else {
            return Ok(None);
        };
        if baseline.is_some_and(|previous| previous != volume) {
            return Ok(None);
        }
        baseline = Some(volume);
    }
    Ok(baseline)
}

fn b_cohorts(
    state: &State,
    current: &Sample,
    crossing_m5: Option<f64>,
) -> (Vec<&'static str>, Option<bool>) {
    let mut names = vec!["all"];
    if state.risk_status != "PASS" {
        return (names, None);
    }
    names.push("pass");
    let recent = (|| {
        let m5 = current.volume_m5?;
        let h1 = current.volume_h1?;
        let baseline = crossing_m5?;
        Some(m5 > 0.0 && h1 > 0.0 && m5 >= h1 / 12.0 && m5 >= baseline * 0.8)
    })();
    let distance = current.price.zip(state.reference_upper).map(|(p, upper)| {
        let line = upper * 1.005;
        if p <= line * 1.03 {
            "pass_distance_0_3pct"
        } else if p <= line * 1.05 {
            "pass_distance_3_5pct"
        } else if p <= line * 1.08 {
            "pass_distance_5_8pct"
        } else {
            "pass_distance_gt8pct"
        }
    });
    let near = distance == Some("pass_distance_0_3pct");
    if recent == Some(true) {
        names.push("pass_recent_volume");
    }
    if near {
        names.push("pass_near_breakout");
    }
    if recent == Some(true) && near {
        names.push("pass_both");
    }
    if let Some(bucket) = distance {
        names.push(bucket);
    }
    (names, recent)
}

fn a_cohorts(state: &State, current: &Sample) -> Vec<&'static str> {
    let mut names = Vec::new();
    if state.risk_status != "PASS" {
        return names;
    }
    names.push("pass");
    let recent = current
        .volume_m5
        .zip(current.volume_h1)
        .map(|(m5, h1)| m5 > 0.0 && h1 > 0.0 && m5 >= h1 / 12.0);
    let line = state.reference_upper.map(|upper| upper * 1.005);
    let near = current
        .price
        .zip(line)
        .map(|(price, upper)| price <= upper * 1.03)
        .unwrap_or(false);
    if recent == Some(true) {
        names.push("pass_recent_volume");
    }
    if near {
        names.push("pass_near_breakout");
    }
    if recent == Some(true) && near {
        names.push("pass_both");
    }
    names
}

fn new_pending(
    keys: Vec<String>,
    at: DateTime<Utc>,
    token: &str,
    pair: &str,
    price: f64,
) -> Pending {
    Pending {
        keys,
        token: token.into(),
        at,
        pair: pair.into(),
        price,
        previous: (at, price),
        remaining: HORIZONS.to_vec(),
        path_exit: None,
        strategy_exits: EXIT_STRATEGY_NAMES
            .into_iter()
            .map(|name| (name.into(), None))
            .collect(),
        ladder_high_pct: 0.0,
        ladder_first: false,
        ladder_second: false,
    }
}

fn record_event_start(hits: &mut BTreeMap<String, Hits>, keys: &[String], sample: &Sample) {
    for key in keys {
        let hit = hits.entry(key.clone()).or_default();
        hit.events += 1;
        hit.first_at = Some(hit.first_at.unwrap_or(sample.at).min(sample.at));
        hit.last_at = Some(hit.last_at.unwrap_or(sample.at).max(sample.at));
        for outcome in hit.forward.values_mut() {
            outcome.denominator += 1;
        }
        hit.path.denominator += 1;
        for outcome in hit.exit_strategies.values_mut() {
            outcome.denominator += 1;
            outcome.record_denominator();
        }
    }
}

#[derive(Default)]
struct TokenReplay {
    from: i64,
    reference_rows: VecDeque<i64>,
    state: Option<State>,
    history: VecDeque<Sample>,
    overflow_until: Option<i64>,
    previous_reference: Option<DateTime<Utc>>,
    pending: Vec<Pending>,
    delayed: Vec<DelayedEntry>,
    portfolio_events: Vec<PortfolioEvent>,
}
fn flush(
    conn: &Connection,
    token: &str,
    rows: &mut Vec<Row>,
    replay: &mut TokenReplay,
    proxy: bool,
    settings: &Settings,
    input: &mut Counts,
    coverage: &mut Counts,
    hits: &mut BTreeMap<String, Hits>,
) -> Result<()> {
    rows.sort_by(|a, b| {
        a.at.cmp(&b.at)
            .then(a.group.cmp(&b.group))
            .then(a.pair.cmp(&b.pair))
    });
    let mut index = 0;
    while index < rows.len() {
        let start = index;
        while index < rows.len()
            && rows[index].at == rows[start].at
            && rows[index].group == rows[start].group
        {
            index += 1;
        }
        replay.group(
            conn,
            token,
            &rows[start..index],
            proxy,
            settings,
            input,
            coverage,
            hits,
        )?;
    }
    rows.clear();
    Ok(())
}
impl TokenReplay {
    fn group(
        &mut self,
        conn: &Connection,
        token: &str,
        group: &[Row],
        proxy: bool,
        settings: &Settings,
        input: &mut Counts,
        coverage: &mut Counts,
        hits: &mut BTreeMap<String, Hits>,
    ) -> Result<()> {
        let Self {
            from,
            reference_rows,
            state,
            history,
            overflow_until,
            previous_reference,
            pending,
            delayed,
            portfolio_events,
        } = self;
        let at = group[0].at;
        count(input, "groups");
        if group.len() > 3 {
            count(input, "groups_over_three_pools");
        }
        // Forward labels consume later data separately; detection receives no future rows.
        labels(pending, group, at, hits, portfolio_events);
        delayed.retain(|entry| {
            if at < entry.target {
                return true;
            }
            if at > entry.target + Duration::seconds(PATH_GRACE_SECONDS) {
                return false;
            }
            if let Some(sample) = group
                .iter()
                .find(|row| row.pair == entry.pair)
                .and_then(|row| row.sample.as_ref())
                .filter(|sample| sample.price.is_some_and(|price| price > 0.0))
            {
                let key = entry.key.clone();
                pending.push(new_pending(
                    vec![key.clone()],
                    at,
                    &entry.token,
                    &entry.pair,
                    sample.price.unwrap(),
                ));
                record_event_start(hits, &[key], sample);
                if let Some(state) = state.as_ref() {
                    if let Some(hit) = hits.get_mut(&entry.key) {
                        count(&mut hit.risk_status, &state.risk_status);
                        count(&mut hit.data_status, &state.data_status);
                    }
                }
                return false;
            }
            true
        });
        if state
            .as_ref()
            .is_some_and(|s| at.timestamp().saturating_sub(s.first_seen_at) >= 7 * 86400)
        {
            *state = None;
            history.clear();
            reference_rows.clear();
            *previous_reference = None;
            *overflow_until = None;
        }
        if state.is_none() {
            if let Some(first) = group
                .iter()
                .filter_map(|r| r.sample.as_ref())
                .filter(|s| s.price.is_some_and(|p| p > 0.0) && s.liquidity_usd.is_some())
                .max_by(|a, b| {
                    a.liquidity_usd
                        .unwrap()
                        .total_cmp(&b.liquidity_usd.unwrap())
                        .then_with(|| b.pair_address.cmp(&a.pair_address))
                })
            {
                *state = Some(State::new(token, first));
                (*history, *reference_rows) =
                    initial_history(conn, token, &first.pair_address, first.at, proxy, *from)?;
                count(coverage, "frozen_tokens");
            } else {
                count(coverage, "no_eligible_reference_groups");
                return Ok(());
            }
        }
        let state = state.as_mut().unwrap();
        let previous_evidence = state.last_evidence_at;
        let old_first = state.first_seen_at;
        let old_stage = state.stage.clone();
        let old_terminated = state.terminated;
        let old_b = state.b_at;
        let old_c = state.c_at;
        signal_v2::tick(state, at.timestamp());
        let Some(reference) = group.iter().find(|r| r.pair == state.pair_address) else {
            count(coverage, "reference_missing_groups");
            count(coverage, &format!("data_status:{}", state.data_status));
            return Ok(());
        };
        count(coverage, "frozen_pool_rows");
        if let Some(previous) = *previous_reference {
            count(coverage, "reference_intervals");
            if at - previous > Duration::seconds(300) {
                count(coverage, "reference_intervals_over_5m");
            }
        }
        *previous_reference = Some(at);
        while history
            .front()
            .is_some_and(|s| s.at < at.timestamp().saturating_sub(86400))
        {
            history.pop_front();
        }
        while reference_rows
            .front()
            .is_some_and(|time| *time < at.timestamp().saturating_sub(86400))
        {
            reference_rows.pop_front();
        }
        let history_overflow = reference_rows.len() > 4096
            || overflow_until.is_some_and(|until| at.timestamp() < until);
        reference_rows.push_back(at.timestamp());
        // shortcut: cap raw history counts at 4097; discarded timestamps block evidence until they age out.
        if reference_rows.len() > 4097 {
            *overflow_until = Some(reference_rows.pop_front().unwrap().saturating_add(86400));
        }
        let Some(current) = reference
            .sample
            .as_ref()
            .filter(|s| s.price.is_some_and(|p| p > 0.0))
        else {
            cancel(state, at.timestamp(), settings);
            count(coverage, "invalid_reference_groups");
            return Ok(());
        };
        let mut risks = Vec::new();
        let scores: Vec<_> = group.iter().filter_map(|r| r.score).collect();
        let missing = group.iter().any(|r| r.score.is_none() || r.risks.is_none());
        let conflict = scores.windows(2).any(|w| w[0] != w[1]);
        if missing {
            count(coverage, "signal_missing_groups");
            risks.push("HOLD".into());
        }
        if conflict {
            count(coverage, "signal_conflict_groups");
            risks.push("HOLD".into());
        }
        for row in group {
            if let Some(flags) = &row.risks {
                risks.extend(flags.iter().cloned());
            }
        }
        let score = if missing || conflict {
            None
        } else {
            scores.first().copied()
        };
        if history_overflow {
            cancel(state, at.timestamp(), settings);
            count(coverage, "history_limit_groups");
        } else {
            let history = history.make_contiguous();
            signal_v2::advance(state, current, history, score, &risks, settings);
        }
        if state.last_evidence_at != previous_evidence {
            count(coverage, "accepted_evidence_groups");
            if let Some(previous) = previous_evidence {
                count(coverage, "accepted_evidence_intervals");
                if current.at.saturating_sub(previous) > 300 {
                    count(coverage, "accepted_evidence_gaps_over_5m");
                }
            }
        } else {
            count(coverage, "duplicate_or_unchanged_groups");
        }
        if state.data_status == "warming_up" {
            count(coverage, "warming_up_groups");
        }
        count(coverage, &format!("data_status:{}", state.data_status));
        count(coverage, &format!("risk_status:{}", state.risk_status));
        if let Some(reason) = &state.c_reason {
            count(coverage, &format!("c_reason:{reason}"));
        }
        let a = state.stage == "watch"
            && (old_stage == "inactive" || old_terminated || state.first_seen_at != old_first);
        let c_candidate = state.stage == "base"
            && old_stage != "base"
            && state.b_at.is_some()
            && state.c_at.is_none()
            && !state.terminated
            && !state.expired
            && state.data_status == "current";
        for (stage, trigger) in [
            ("A", a),
            ("B", old_b.is_none() && state.b_at.is_some()),
            ("C", old_c.is_none() && state.c_at.is_some()),
            ("C/candidate", c_candidate),
        ] {
            if !trigger {
                continue;
            }
            let mut keys = vec![stage.to_string()];
            if stage == "A" {
                for name in a_cohorts(state, current) {
                    keys.push(format!("A/{name}"));
                }
                for delay in A_ENTRY_DELAYS_SECONDS {
                    delayed.push(DelayedEntry {
                        key: format!("A/delay_{delay}s"),
                        token: token.to_string(),
                        pair: state.pair_address.clone(),
                        target: at + Duration::seconds(delay),
                    });
                }
            }
            if stage == "B" {
                let baseline = if state.risk_status == "PASS" {
                    crossing_m5(conn, token, state, proxy)?
                } else {
                    None
                };
                let (names, recent) = b_cohorts(state, current, baseline);
                if state.risk_status == "PASS" {
                    count(
                        coverage,
                        match recent {
                            Some(true) => "b_recent_volume:pass",
                            Some(false) => "b_recent_volume:fail",
                            None => "b_recent_volume:unknown",
                        },
                    );
                }
                for name in names {
                    keys.push(format!("B/{name}"));
                    keys.push(format!("B/date/{}/{name}", at.date_naive()));
                }
            }
            for key in &keys {
                let hit = hits.entry(key.clone()).or_default();
                count(&mut hit.risk_status, &state.risk_status);
                count(&mut hit.data_status, &state.data_status);
            }
            record_event_start(hits, &keys, current);
            pending.push(new_pending(
                keys,
                at,
                token,
                &state.pair_address,
                current.price.unwrap(),
            ));
        }
        history.push_back(current.clone());
        if history.len() > 4097 {
            history.pop_front();
        }
        Ok(())
    }
    fn finish(&mut self, end: DateTime<Utc>, hits: &mut BTreeMap<String, Hits>) {
        for pending in self.pending.drain(..) {
            let deadline =
                pending.at + Duration::seconds(PATH_HORIZON_SECONDS + PATH_GRACE_SECONDS);
            if pending.path_exit.is_none() {
                for key in &pending.keys {
                    let path = &mut hits.get_mut(key).unwrap().path;
                    if end < deadline {
                        path.unresolved += 1;
                    } else {
                        path.endpoint_missing += 1;
                    }
                }
            }
            for (name, exit) in &pending.strategy_exits {
                if exit.is_none() {
                    for key in &pending.keys {
                        let strategy = hits
                            .get_mut(key)
                            .unwrap()
                            .exit_strategies
                            .get_mut(name)
                            .unwrap();
                        if end < deadline {
                            strategy.unresolved += 1;
                            strategy.record_incomplete(true);
                        } else {
                            strategy.endpoint_missing += 1;
                            strategy.record_incomplete(false);
                        }
                    }
                }
            }
            for (name, horizon) in pending.remaining {
                for key in &pending.keys {
                    let outcome = hits.get_mut(key).unwrap().forward.get_mut(name).unwrap();
                    if end < pending.at + Duration::seconds(horizon + 300) {
                        outcome.right_censored += 1;
                    } else {
                        outcome.endpoint_missing += 1;
                    }
                }
            }
        }
    }
}

fn labels(
    pending: &mut Vec<Pending>,
    group: &[Row],
    now: DateTime<Utc>,
    hits: &mut BTreeMap<String, Hits>,
    portfolio_events: &mut Vec<PortfolioEvent>,
) {
    for event in pending.iter_mut() {
        let current = group
            .iter()
            .find(|r| r.pair == event.pair)
            .and_then(|r| r.sample.as_ref())
            .and_then(|s| s.price)
            .filter(|p| *p > 0.0);
        event.remaining.retain(|(name, horizon)| {
            let target = event.at + Duration::seconds(*horizon);
            if now < target {
                return true;
            }
            let resolved = if now > target + Duration::seconds(300) {
                Some(None)
            } else if let Some(price) = current {
                if event.previous.0 <= target && now - event.previous.0 <= Duration::seconds(300) {
                    let value = (price / event.price - 1.0) * 100.0;
                    Some(value.is_finite().then_some(value))
                } else {
                    Some(None)
                }
            } else {
                None
            };
            if let Some(value) = resolved {
                for key in &event.keys {
                    let outcome = hits.get_mut(key).unwrap().forward.get_mut(*name).unwrap();
                    if let Some(value) = value {
                        outcome.record(value);
                    } else {
                        outcome.endpoint_missing += 1;
                    }
                }
            }
            resolved.is_none()
        });
        if let Some(price) = current {
            let deadline = event.at + Duration::seconds(PATH_HORIZON_SECONDS + PATH_GRACE_SECONDS);
            let gross_return_pct = (price / event.price - 1.0) * 100.0;
            let fixed_was_new = event.path_exit.is_some();
            let no_target_was_set = event
                .strategy_exits
                .get("no_fixed_take_profit")
                .is_some_and(Option::is_some);
            let ladder_was_set = event
                .strategy_exits
                .get("ladder_take_profit")
                .is_some_and(Option::is_some);
            if event.path_exit.is_none() {
                event.path_exit = if now > deadline {
                    Some(("timeout", gross_return_pct))
                } else if gross_return_pct >= PATH_TARGET_PCT {
                    Some(("target_first", PATH_TARGET_PCT))
                } else if gross_return_pct <= PATH_STOP_PCT {
                    Some(("stop_first", PATH_STOP_PCT))
                } else {
                    None
                };
                if let Some((kind, gross_return_pct)) = event.path_exit {
                    for key in &event.keys {
                        hits.get_mut(key)
                            .unwrap()
                            .path
                            .record_exit(kind, gross_return_pct);
                    }
                }
            }
            if let Some(exit) = event.path_exit {
                let fixed = event.strategy_exits.get_mut("fixed_take_profit").unwrap();
                if fixed.is_none() {
                    *fixed = Some(exit);
                }
            }
            let no_target = if now > deadline {
                Some(("timeout", gross_return_pct))
            } else if gross_return_pct <= PATH_STOP_PCT {
                Some(("stop", PATH_STOP_PCT))
            } else {
                None
            };
            if no_target.is_some() {
                *event
                    .strategy_exits
                    .get_mut("no_fixed_take_profit")
                    .unwrap() = no_target;
            }
            if event
                .strategy_exits
                .get("ladder_take_profit")
                .is_some_and(Option::is_none)
            {
                event.ladder_high_pct = event.ladder_high_pct.max(gross_return_pct);
                let mut milestones = Vec::new();
                if !event.ladder_first {
                    if gross_return_pct <= PATH_STOP_PCT {
                        *event.strategy_exits.get_mut("ladder_take_profit").unwrap() =
                            Some(("stop", PATH_STOP_PCT));
                    } else if gross_return_pct >= PATH_TARGET_PCT {
                        event.ladder_first = true;
                        milestones.push("first_target");
                    } else if now > deadline {
                        *event.strategy_exits.get_mut("ladder_take_profit").unwrap() =
                            Some(("timeout", gross_return_pct));
                    }
                }
                if event
                    .strategy_exits
                    .get("ladder_take_profit")
                    .is_some_and(Option::is_none)
                    && event.ladder_first
                {
                    if !event.ladder_second && gross_return_pct >= 25.0 {
                        event.ladder_second = true;
                        milestones.push("second_target");
                    }
                    let stop_line_pct = if event.ladder_second {
                        (event.ladder_high_pct - 20.0).max(8.0)
                    } else {
                        0.0
                    };
                    if gross_return_pct <= stop_line_pct {
                        let locked_pct = if event.ladder_second { 10.5 } else { 3.0 };
                        let remaining_pct = if event.ladder_second { 0.4 } else { 0.7 };
                        let blended_pct = locked_pct + remaining_pct * stop_line_pct;
                        *event.strategy_exits.get_mut("ladder_take_profit").unwrap() =
                            Some(("trailing_stop", blended_pct));
                    } else if now > deadline {
                        let locked_pct = if event.ladder_second { 10.5 } else { 3.0 };
                        let remaining_pct = if event.ladder_second { 0.4 } else { 0.7 };
                        let blended_pct = locked_pct + remaining_pct * gross_return_pct;
                        *event.strategy_exits.get_mut("ladder_take_profit").unwrap() =
                            Some(("timeout", blended_pct));
                    }
                }
                for milestone in milestones {
                    for key in &event.keys {
                        hits.get_mut(key)
                            .unwrap()
                            .exit_strategies
                            .get_mut("ladder_take_profit")
                            .unwrap()
                            .record_milestone(milestone);
                    }
                }
            }
            let newly_exits = [
                (
                    "fixed_take_profit",
                    !fixed_was_new && event.path_exit.is_some(),
                ),
                (
                    "no_fixed_take_profit",
                    !no_target_was_set
                        && event
                            .strategy_exits
                            .get("no_fixed_take_profit")
                            .is_some_and(Option::is_some),
                ),
                (
                    "ladder_take_profit",
                    !ladder_was_set
                        && event
                            .strategy_exits
                            .get("ladder_take_profit")
                            .is_some_and(Option::is_some),
                ),
            ];
            for (name, is_new) in newly_exits {
                if is_new {
                    let exit = event.strategy_exits.get(name).unwrap().unwrap();
                    if name == "ladder_take_profit"
                        && event.keys.iter().any(|key| key == "A/pass_recent_volume")
                    {
                        portfolio_events.push(PortfolioEvent {
                            token: event.token.clone(),
                            entry_at: event.at.timestamp(),
                            exit_at: now.timestamp(),
                            gross_return_pct: exit.1,
                        });
                    }
                    for key in &event.keys {
                        hits.get_mut(key)
                            .unwrap()
                            .exit_strategies
                            .get_mut(name)
                            .unwrap()
                            .record_exit(exit.0, exit.1);
                    }
                }
            }
            event.previous = (now, price);
        }
    }
    pending.retain(|event| !event.remaining.is_empty() || event.path_exit.is_none());
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::{params, Connection};
    struct Database(String);
    impl Database {
        fn new() -> Self {
            static NEXT_DATABASE: std::sync::atomic::AtomicU64 =
                std::sync::atomic::AtomicU64::new(0);
            let path = std::env::temp_dir().join(format!(
                "tmm-replay-{}-{}-{}.db",
                std::process::id(),
                chrono::Utc::now().timestamp_nanos_opt().unwrap(),
                NEXT_DATABASE.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
            ));
            let path = path.to_str().unwrap().to_string();
            crate::db::init_schema(&path).unwrap();
            Self(path)
        }
        fn insert(&self, time: i64, pair: &str, price: f64, h1: f64, liquidity: f64, strict: bool) {
            let at = chrono::DateTime::from_timestamp(time, 0)
                .unwrap()
                .to_rfc3339();
            let raw = if strict {
                json!({"signal_v2":{"version":1,"provider":"dexscreener","request_id":format!("r{time}"),"request_completed_at":at,"source_time_unverified":true,"presence":{"price_usd":true,"volume_h1":true,"volume_m5":true,"liquidity_usd":true}}})
            } else {
                json!({})
            };
            let conn = Connection::open(&self.0).unwrap();
            conn.execute("INSERT INTO snapshots(pair_address,token_address,observed_at,price_usd,volume_h1,volume_m5,liquidity_usd,raw_json) VALUES (?1,'token',?2,?3,?4,0,?5,?6)", params![pair,at,price,h1,liquidity,raw.to_string()]).unwrap();
            conn.execute("INSERT INTO signals(pair_address,token_address,observed_at,strategy_version,score,pair_state) VALUES (?1,'token',?2,'legacy',70,'active')",params![pair,at]).unwrap();
        }
        fn replay(&self, proxy: bool) -> Value {
            let args = if proxy {
                vec!["--legacy-proxy".into()]
            } else {
                vec![]
            };
            run(&self.0, &args, &Settings::default()).unwrap()
        }
    }
    impl Drop for Database {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }
    const T: i64 = 100 * 86400;
    fn breakout(db: &Database) {
        for hour in 2..=24 {
            db.insert(
                T - hour * 3600,
                "a",
                100.0,
                10000.0,
                50000.0 + hour as f64,
                true,
            );
        }
        for (i, price) in [100.0, 100.1, 100.2, 100.3, 101.0].iter().enumerate() {
            db.insert(
                T - 3600 + i as i64 * 900,
                "a",
                *price,
                30000.0,
                60000.0 + i as f64,
                true,
            );
        }
        // The three confirmation observations span four minutes, each gap <= five minutes.
        db.insert(T + 120, "a", 101.3, 30000.0, 70001.0, true);
        db.insert(T + 240, "a", 101.4, 30000.0, 70002.0, true);
    }
    #[test]
    fn replay_direction_rate_excludes_missing_endpoints_and_counts_flat_separately() {
        let db = Database::new();
        for (pair, end) in [
            ("up", 110.0),
            ("down", 90.0),
            ("flat", 100.0),
            ("missing", 120.0),
        ] {
            db.insert(T, pair, 100.0, 30000.0, 50000.0, true);
            db.insert(T + 840, pair, 100.1, 30000.0, 50000.0, true);
            db.insert(
                T + if pair == "missing" { 1500 } else { 960 },
                pair,
                end,
                30000.0,
                50000.0,
                true,
            );
        }
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("UPDATE snapshots SET token_address=pair_address", [])
            .unwrap();
        conn.execute("UPDATE signals SET token_address=pair_address", [])
            .unwrap();
        let report = db.replay(false);
        let out = &report["structural_hits"]["A"]["forward"]["15m"];
        assert_eq!(out["denominator"], 4);
        assert_eq!(out["eligible"], 3);
        assert_eq!(out["endpoint_missing"], 1);
        assert_eq!(out["positive"], 1);
        assert_eq!(out["negative"], 1);
        assert_eq!(out["flat"], 1);
        assert!(
            (out["positive_sampled_return_rate_pct"].as_f64().unwrap() - 33.333333333333336).abs()
                < 1e-10
        );
        assert!(report["structural_hits"]["A"]["forward"]["4h"]
            ["positive_sampled_return_rate_pct"]
            .is_null());
    }
    #[test]
    fn replay_b_cohorts_use_trigger_evidence_and_preserve_original_events() {
        let db = Database::new();
        breakout(&db);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("UPDATE snapshots SET volume_m5=3000", [])
            .unwrap();
        let before = db.replay(false);
        assert_eq!(before["structural_hits"]["A"]["events"], 1);
        assert_eq!(before["structural_hits"]["B"]["events"], 1);
        for cohort in [
            "all",
            "pass",
            "pass_recent_volume",
            "pass_near_breakout",
            "pass_both",
            "pass_distance_0_3pct",
        ] {
            assert_eq!(
                before["b_filter_comparison"]["cohorts"][cohort]["events"], 1,
                "{cohort}"
            );
            assert_eq!(
                before["b_filter_comparison"]["by_utc_date"]["1970-04-11"][cohort]["events"],
                1
            );
        }
        // Later data changes volume/liquidity and supplies a covered +10% label.
        for (time, price) in [(T + 1080, 101.5), (T + 1200, 111.54)] {
            db.insert(time, "a", price, 1.0, 1.0, true);
        }
        let after = db.replay(false);
        assert_eq!(after["structural_hits"]["B"]["events"], 1);
        assert_eq!(
            after["b_filter_comparison"]["cohorts"]["pass_both"]["events"],
            1
        );
        let out = &after["b_filter_comparison"]["cohorts"]["pass_both"]["forward"]["15m"];
        assert_eq!(out["eligible"], 1);
        assert_eq!(out["positive"], 1);
        assert_eq!(out["positive_sampled_return_rate_pct"], 100.0);
        assert!((out["mean_sampled_return_pct"].as_f64().unwrap() - 10.0).abs() < 1e-10);
        assert_eq!(after["opportunity_eligible_events"], 0);
    }
    #[test]
    fn replay_a_cohorts_use_trigger_evidence_without_waiting_for_b() {
        let db = Database::new();
        breakout(&db);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("UPDATE snapshots SET volume_m5=3000", [])
            .unwrap();
        let report = db.replay(false);
        assert_eq!(report["structural_hits"]["A"]["events"], 1);
        for cohort in ["pass", "pass_recent_volume"] {
            assert_eq!(
                report["structural_hits"][format!("A/{cohort}")]["events"],
                1,
                "{cohort}"
            );
        }
    }
    #[test]
    fn replay_c_candidate_starts_when_base_forms_after_b() {
        let db = Database::new();
        breakout(&db);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("UPDATE snapshots SET volume_m5=3000", [])
            .unwrap();
        for (time, price) in [
            (T + 600, 112.0),
            (T + 840, 108.0),
            (T + 1080, 108.1),
            (T + 1320, 108.2),
            (T + 1560, 108.1),
            (T + 1800, 108.2),
        ] {
            db.insert(time, "a", price, 30000.0, 70003.0, true);
        }
        db.insert(T + 360, "a", 101.2, 30000.0, 70003.0, true);
        conn.execute("UPDATE snapshots SET volume_m5=3000", [])
            .unwrap();
        let report = db.replay(false);
        assert_eq!(report["structural_hits"]["C"]["events"], 0);
        assert_eq!(report["structural_hits"]["C/candidate"]["events"], 1);
    }
    #[test]
    fn replay_a_delayed_entries_sample_the_future_price_once() {
        let db = Database::new();
        breakout(&db);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("UPDATE snapshots SET volume_m5=3000", [])
            .unwrap();
        for delay in [60, 300, 900, 1800, 3600] {
            let observed_delay = match delay {
                60 => 60,
                300 => 540,
                900 => 900,
                1800 => 2100,
                _ => 3900,
            };
            db.insert(
                T - 86_400 + observed_delay,
                "a",
                102.0,
                30000.0,
                70003.0,
                true,
            );
            db.insert(
                T - 86_400 + observed_delay + 600,
                "a",
                103.0,
                30000.0,
                70005.0,
                true,
            );
            db.insert(
                T - 86_400 + observed_delay + 900,
                "a",
                112.0,
                30000.0,
                70004.0,
                true,
            );
        }
        let report = db.replay(false);
        for delay in [60, 300, 900, 1800, 3600] {
            let cohort = &report["structural_hits"][format!("A/delay_{delay}s")];
            assert_eq!(cohort["events"], 1, "{delay}");
            assert_eq!(cohort["forward"]["15m"]["denominator"], 1, "{delay}");
            if [60, 300, 900].contains(&delay) {
                let expected = (112.0 / 102.0 - 1.0) * 100.0;
                let actual = cohort["forward"]["15m"]["mean_sampled_return_pct"]
                    .as_f64()
                    .unwrap();
                assert!((actual - expected).abs() < 1e-9, "{delay}");
            }
        }
    }
    #[test]
    fn portfolio_simulation_respects_position_capacity() {
        let events = vec![
            PortfolioEvent {
                token: "a".into(),
                entry_at: 0,
                exit_at: 100,
                gross_return_pct: 10.0,
            },
            PortfolioEvent {
                token: "b".into(),
                entry_at: 50,
                exit_at: 150,
                gross_return_pct: -10.0,
            },
        ];
        let policies = simulate_portfolio(&events);
        let one = &policies["max_positions_1"];
        assert_eq!(one.opened, 1);
        assert_eq!(one.skipped_capacity, 1);
        assert!((one.final_equity - 1.097).abs() < 1e-9);
        let two = &policies["max_positions_2"];
        assert_eq!(two.opened, 2);
        assert_eq!(two.skipped_capacity, 0);
        assert!((two.final_equity - 0.99450225).abs() < 1e-8);
    }
    #[test]
    fn replay_adds_strong_a_cohort_to_portfolio_simulation() {
        let db = Database::new();
        breakout(&db);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("UPDATE snapshots SET volume_m5=3000", [])
            .unwrap();
        db.insert(T + 15_600, "a", 110.0, 30000.0, 70003.0, true);
        let report = db.replay(false);
        let portfolio = &report["portfolio"];
        assert_eq!(portfolio["events"], 1);
        assert_eq!(portfolio["policies"]["max_positions_1"]["opened"], 1);
        assert_eq!(portfolio["policies"]["max_positions_5"]["opened"], 1);
    }
    #[test]
    fn replay_path_outcome_separates_target_and_stop_before_changing_win_rate() {
        for (later_price, outcome, net, profit_factor) in [
            (112.0, "target_first", 10.0, None),
            (89.0, "stop_first", -10.0, Some(0.0)),
        ] {
            let db = Database::new();
            breakout(&db);
            db.insert(T + 480, "a", later_price, 1.0, 1.0, true);
            let report = db.replay(false);
            let path = &report["structural_hits"]["B"]["path"];
            assert_eq!(path["denominator"], 1, "{later_price}");
            assert_eq!(path[outcome], 1, "{later_price}");
            assert_eq!(path["resolved"], 1, "{later_price}");
            assert!((path["mean_net_return_pct"].as_f64().unwrap() - net).abs() < 1e-9);
            assert_eq!(
                path["profit_factor"].as_f64(),
                profit_factor,
                "{later_price}"
            );
        }
    }
    #[test]
    fn replay_exit_strategies_compare_fixed_no_target_and_ladder_rules() {
        let db = Database::new();
        breakout(&db);
        for (time, price) in [
            (T + 480, 112.0),
            (T + 600, 130.0),
            (T + 720, 100.0),
            (T + 15_000, 120.0),
        ] {
            db.insert(time, "a", price, 1.0, 1.0, true);
        }
        let report = db.replay(false);
        let strategies = &report["structural_hits"]["B"]["exit_strategies"];
        assert_eq!(strategies["fixed_take_profit"]["target_first"], 1);
        let sensitivity = strategies["fixed_take_profit"]["cost_sensitivity"]
            .as_array()
            .unwrap();
        assert_eq!(sensitivity.len(), 4);
        for (index, expected_cost) in [0.0, 0.3, 0.5, 1.0].into_iter().enumerate() {
            assert_eq!(sensitivity[index]["cost_pct"], expected_cost);
            assert_eq!(sensitivity[index]["denominator"], 1);
            assert_eq!(sensitivity[index]["resolved"], 1);
            assert!(
                (sensitivity[index]["mean_net_return_pct"].as_f64().unwrap()
                    - (10.0 - expected_cost))
                    .abs()
                    < 1e-9
            );
        }
        let no_target = &strategies["no_fixed_take_profit"];
        assert_eq!(no_target["timeout"], 1);
        assert_eq!(no_target["stop"], 0);
        assert!(no_target["mean_net_return_pct"].as_f64().unwrap() > 0.0);
        let ladder = &strategies["ladder_take_profit"];
        assert_eq!(ladder["first_target"], 1);
        assert_eq!(ladder["second_target"], 1);
        assert_eq!(ladder["trailing_stop"], 1);
        assert!(ladder["mean_net_return_pct"].as_f64().unwrap() > 0.0);
    }
    #[test]
    fn ladder_exits_at_timeout_even_when_the_first_target_is_never_reached() {
        let db = Database::new();
        breakout(&db);
        db.insert(T + 15_000, "a", 100.0, 1.0, 1.0, true);
        let ladder =
            &db.replay(false)["structural_hits"]["B"]["exit_strategies"]["ladder_take_profit"];
        assert_eq!(ladder["timeout"], 1);
        assert_eq!(ladder["first_target"], 0);
        assert!(ladder["mean_net_return_pct"].as_f64().unwrap() < 0.0);
    }
    #[test]
    fn replay_b_recent_volume_tests_share_retention_and_missing_separately() {
        for (cross, current, absent, want, recent_count) in [
            (3000.0, 2500.0, false, "pass", 1),
            (3000.0, 2499.0, false, "fail", 0),
            (4000.0, 3000.0, false, "fail", 0),
            (4000.0, 3200.0, false, "pass", 1),
            (3000.0, 3000.0, true, "unknown", 0),
        ] {
            let db = Database::new();
            breakout(&db);
            let conn = Connection::open(&db.0).unwrap();
            conn.execute("UPDATE snapshots SET volume_m5=?1", [cross])
                .unwrap();
            let at = DateTime::from_timestamp(T + 240, 0).unwrap().to_rfc3339();
            conn.execute(
                "UPDATE snapshots SET volume_m5=?1 WHERE observed_at=?2",
                params![current, at],
            )
            .unwrap();
            if absent {
                conn.execute("UPDATE snapshots SET raw_json=json_set(raw_json,'$.signal_v2.presence.volume_m5',json('false')) WHERE observed_at=?1", [&at]).unwrap();
            }
            let report = db.replay(false);
            let c = &report["b_filter_comparison"];
            assert_eq!(report["structural_hits"]["B"]["events"], 1);
            assert_eq!(
                c["recent_volume_evidence"][want], 1,
                "{cross}/{current}/{absent}"
            );
            assert_eq!(c["cohorts"]["pass"]["events"], 1);
            assert_eq!(c["cohorts"]["pass_recent_volume"]["events"], recent_count);
        }
    }
    #[test]
    fn replay_b_risk_block_does_not_enter_pass_or_distance_cohorts() {
        let db = Database::new();
        breakout(&db);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("UPDATE snapshots SET volume_m5=3000", [])
            .unwrap();
        conn.execute("UPDATE signals SET risk_flags='[\"BLOCK\"]'", [])
            .unwrap();
        let report = db.replay(false);
        assert_eq!(report["structural_hits"]["B"]["events"], 1);
        assert_eq!(report["b_filter_comparison"]["cohorts"]["all"]["events"], 1);
        for (name, cohort) in report["b_filter_comparison"]["cohorts"]
            .as_object()
            .unwrap()
        {
            if name != "all" {
                assert_eq!(cohort["events"], 0, "{name}");
            }
        }
    }
    #[test]
    fn replay_b_same_second_conflicting_crossing_volume_is_unknown() {
        let db = Database::new();
        breakout(&db);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("UPDATE snapshots SET volume_m5=3000", [])
            .unwrap();
        db.insert(T + 1, "a", 101.0, 30000.0, 60000.0, true);
        let at = DateTime::from_timestamp(T, 100_000_000)
            .unwrap()
            .to_rfc3339();
        conn.execute("UPDATE snapshots SET volume_m5=9000, observed_at=?1,raw_json=json_set(raw_json,'$.signal_v2.request_completed_at',?1,'$.signal_v2.request_id','conflicting-cross') WHERE id=(SELECT MAX(id) FROM snapshots)", [&at]).unwrap();
        conn.execute(
            "UPDATE signals SET observed_at=?1 WHERE id=(SELECT MAX(id) FROM signals)",
            [&at],
        )
        .unwrap();
        let report = db.replay(false);
        assert_eq!(report["structural_hits"]["B"]["events"], 1);
        assert_eq!(
            report["b_filter_comparison"]["cohorts"]["pass_recent_volume"]["events"],
            0
        );
        assert_eq!(
            report["b_filter_comparison"]["recent_volume_evidence"]["unknown"],
            1
        );
    }
    #[test]
    fn replay_b_missing_price_crossing_conflict_cannot_disappear_from_filter() {
        let db = Database::new();
        breakout(&db);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("UPDATE snapshots SET volume_m5=3000", [])
            .unwrap();
        let original = DateTime::from_timestamp(T, 0).unwrap().to_rfc3339();
        let valid = DateTime::from_timestamp(T, 200_000_000)
            .unwrap()
            .to_rfc3339();
        conn.execute("UPDATE snapshots SET observed_at=?1,raw_json=json_set(raw_json,'$.signal_v2.request_completed_at',?1) WHERE observed_at=?2",params![valid,original]).unwrap();
        conn.execute(
            "UPDATE signals SET observed_at=?1 WHERE observed_at=?2",
            params![valid, original],
        )
        .unwrap();
        db.insert(T + 1, "a", 101.0, 30000.0, 60000.0, true);
        let bad = DateTime::from_timestamp(T, 100_000_000)
            .unwrap()
            .to_rfc3339();
        conn.execute("UPDATE snapshots SET observed_at=?1,volume_m5=9000,raw_json=json_set(raw_json,'$.signal_v2.request_completed_at',?1,'$.signal_v2.presence.price_usd',json('false')) WHERE id=(SELECT MAX(id) FROM snapshots)",[&bad]).unwrap();
        conn.execute(
            "UPDATE signals SET observed_at=?1 WHERE id=(SELECT MAX(id) FROM signals)",
            [&bad],
        )
        .unwrap();
        let report = db.replay(false);
        assert_eq!(report["structural_hits"]["B"]["events"], 1);
        assert_eq!(report["structural_hits"]["B"]["first_at"], T + 240);
        assert_eq!(
            report["b_filter_comparison"]["cohorts"]["pass_recent_volume"]["events"],
            0
        );
        assert_eq!(
            report["b_filter_comparison"]["recent_volume_evidence"]["unknown"],
            1
        );
    }
    #[test]
    fn replay_b_distance_buckets_keep_inclusive_upper_boundaries() {
        for (multiplier, bucket, near) in [
            (1.03, "pass_distance_0_3pct", 1),
            (1.030001, "pass_distance_3_5pct", 0),
            (1.05, "pass_distance_3_5pct", 0),
            (1.050001, "pass_distance_5_8pct", 0),
            (1.08, "pass_distance_5_8pct", 0),
            (1.080001, "pass_distance_gt8pct", 0),
        ] {
            let db = Database::new();
            breakout(&db);
            let at = DateTime::from_timestamp(T + 240, 0).unwrap().to_rfc3339();
            Connection::open(&db.0)
                .unwrap()
                .execute(
                    "UPDATE snapshots SET price_usd=?1 WHERE observed_at=?2",
                    params![100.3 * 1.005 * multiplier, at],
                )
                .unwrap();
            let report = db.replay(false);
            let cohorts = &report["b_filter_comparison"]["cohorts"];
            assert_eq!(cohorts[bucket]["events"], 1, "{multiplier}");
            assert_eq!(
                cohorts["pass_near_breakout"]["events"], near,
                "{multiplier}"
            );
        }
    }
    #[test]
    fn replay_empty_comparison_has_zero_cohorts_without_inventing_rates() {
        let report = Database::new().replay(false);
        assert!(report["b_filter_comparison"]["by_utc_date"]
            .as_object()
            .unwrap()
            .is_empty());
        for cohort in [
            "all",
            "pass",
            "pass_recent_volume",
            "pass_near_breakout",
            "pass_both",
        ] {
            let c = &report["b_filter_comparison"]["cohorts"][cohort];
            assert_eq!(c["events"], 0);
            assert!(c["forward"]["1h"]["positive_sampled_return_rate_pct"].is_null());
        }
    }
    #[test]
    fn replay_strict_excludes_legacy_and_proxy_keeps_zero_volume_unknown() {
        let db = Database::new();
        db.insert(T, "a", 1.0, 0.0, 50000.0, false);
        let strict = db.replay(false);
        assert_eq!(strict["input"]["accepted_rows"], 0);
        assert_eq!(strict["input"]["excluded_rows"], 1);
        let proxy = db.replay(true);
        assert_eq!(proxy["input"]["proxy_rows"], 1);
        assert_eq!(proxy["coverage"]["missing_h1_rows"], 1);
        assert_eq!(proxy["coverage"]["missing_m5_rows"], 1);
        assert_eq!(proxy["mode"], "legacy_proxy_exploratory");
    }
    #[test]
    fn replay_future_suffix_and_liquidity_ranking_cannot_rewrite_prior_trigger() {
        let db = Database::new();
        breakout(&db);
        let before = db.replay(false);
        assert_eq!(before["structural_hits"]["B"]["events"], 1);
        assert_eq!(before["structural_hits"]["B"]["first_at"], T + 240);
        for time in [T + 480, T + 720] {
            db.insert(time, "a", 101.5, 30000.0, 100.0, true);
            db.insert(time, "z", 9000.0, 999999.0, 999999999.0, true);
        }
        let after = db.replay(false);
        assert_eq!(after["structural_hits"]["B"]["first_at"], T + 240);
        assert_eq!(after["structural_hits"]["B"]["events"], 1);
        assert_eq!(after["coverage"]["reference_switches"], 0);
        assert_eq!(after["opportunity_eligible_events"], 0);
    }
    #[test]
    fn replay_is_read_only_and_reports_zero_hits_and_censored_outcomes() {
        let db = Database::new();
        db.insert(T, "a", 1.0, 30000.0, 50000.0, true);
        let conn = Connection::open(&db.0).unwrap();
        let before: String = conn
            .query_row("SELECT group_concat(sql,';') FROM sqlite_master", [], |r| {
                r.get(0)
            })
            .unwrap();
        let report = db.replay(false);
        assert_eq!(report["structural_hits"]["A"]["events"], 1);
        assert_eq!(report["structural_hits"]["B"]["events"], 0);
        assert_eq!(report["structural_hits"]["C"]["events"], 0);
        assert_eq!(report["coverage"]["warming_up_groups"], 1);
        let forward = &report["structural_hits"]["A"]["forward"]["15m"];
        assert_eq!(forward["denominator"], 1);
        assert_eq!(forward["eligible"], 0);
        assert_eq!(forward["right_censored"], 1);
        assert!(forward["mean_sampled_return_pct"].is_null());
        let after: String = conn
            .query_row("SELECT group_concat(sql,';') FROM sqlite_master", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(before, after);
        for (table, want) in [
            ("snapshots", 1),
            ("signals", 1),
            ("signal_v2_state", 0),
            ("signal_v2_changes", 0),
        ] {
            let count: i64 = conn
                .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| r.get(0))
                .unwrap();
            assert_eq!(count, want);
        }
        assert!(conn
            .is_readonly(rusqlite::DatabaseName::Main)
            .is_ok_and(|read_only| !read_only));
    }
    #[test]
    fn replay_forward_requires_endpoint_coverage_and_never_substitutes_late_prices() {
        let db = Database::new();
        for (time, price) in [
            (T, 100.0),
            (T + 840, 101.0),
            (T + 960, 110.0),
            (T + 4200, 200.0),
            (T + 15000, 300.0),
        ] {
            db.insert(time, "a", price, 30000.0, 50000.0, true);
        }
        let report = db.replay(false);
        let forward = &report["structural_hits"]["A"]["forward"];
        assert_eq!(forward["15m"]["eligible"], 1);
        assert!((forward["15m"]["mean_sampled_return_pct"].as_f64().unwrap() - 10.0).abs() < 1e-9);
        assert_eq!(forward["1h"]["eligible"], 0);
        assert_eq!(forward["1h"]["endpoint_missing"], 1);
        assert_eq!(forward["4h"]["eligible"], 0);
        assert_eq!(forward["4h"]["endpoint_missing"], 1);
    }
    #[test]
    fn replay_uses_prior_same_pool_history_when_liquidity_first_becomes_available() {
        let db = Database::new();
        for hour in 2..=24 {
            db.insert(
                T - hour * 3600,
                "a",
                100.0,
                10000.0 + hour as f64,
                50000.0 + hour as f64,
                true,
            );
        }
        for i in 0..4 {
            db.insert(
                T - 3600 + i * 900,
                "a",
                100.0 + i as f64 * 0.1,
                30000.0,
                60000.0 + i as f64,
                true,
            );
        }
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("UPDATE snapshots SET liquidity_usd=NULL,raw_json=json_set(raw_json,'$.signal_v2.presence.liquidity_usd',json('false'))",[]).unwrap();
        for (time, price) in [
            (T, 100.4),
            (T + 120, 101.0),
            (T + 240, 101.1),
            (T + 360, 101.2),
        ] {
            db.insert(time, "a", price, 30000.0, 70000.0 + price, true);
        }
        let report = db.replay(false);
        assert_eq!(report["structural_hits"]["B"]["events"], 1);
        assert_eq!(report["structural_hits"]["B"]["first_at"], T + 360);
    }
    #[test]
    fn replay_equal_liquidity_tie_uses_lowest_address_and_never_replaces_missing_reference() {
        let db = Database::new();
        db.insert(T, "z", 900.0, 30000.0, 50000.0, true);
        db.insert(T, "a", 100.0, 30000.0, 50000.0, true);
        db.insert(T + 900, "z", 999.0, 30000.0, 999999.0, true);
        let report = db.replay(false);
        assert_eq!(report["input"]["groups"], 2);
        assert_eq!(report["structural_hits"]["A"]["events"], 1);
        assert_eq!(report["coverage"]["reference_missing_groups"], 1);
        assert_eq!(report["coverage"]["accepted_evidence_groups"], 1);
    }
    #[test]
    fn replay_does_not_promote_malformed_metadata_in_proxy_mode() {
        let db = Database::new();
        db.insert(T, "a", 1.0, 30000.0, 50000.0, true);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute(
            "UPDATE snapshots SET raw_json=json_set(raw_json,'$.signal_v2.provider','other')",
            [],
        )
        .unwrap();
        let report = db.replay(true);
        assert_eq!(report["input"]["accepted_rows"], 0);
        assert_eq!(report["input"]["excluded_rows"], 1);
        assert_eq!(
            report["structural_hits"]["A"]["forward"]["1h"]["denominator"],
            0
        );
        assert!(
            report["structural_hits"]["A"]["forward"]["1h"]["mean_sampled_return_pct"].is_null()
        );
    }
    #[test]
    fn replay_counts_unrepresentable_sqlite_time_without_panicking_or_inventing_hits() {
        let db = Database::new();
        db.insert(T, "a", 1.0, 30000.0, 50000.0, true);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute(
            "UPDATE snapshots SET observed_at='1998-12-31T23:59:60+00:00'",
            [],
        )
        .unwrap();
        let report = db.replay(false);
        assert_eq!(report["input"]["invalid_time_rows"], 1);
        assert_eq!(report["input"]["window_rows"], 0);
        assert_eq!(report["structural_hits"]["A"]["events"], 0);
        assert!(report["window"]["replay_from"].is_null());
    }
    #[test]
    fn replay_rejects_nonpositive_focus_thresholds_before_missing_scores_can_enter_a() {
        let db = Database::new();
        db.insert(T, "a", 100.0, 30000.0, 50000.0, true);
        Connection::open(&db.0)
            .unwrap()
            .execute("DELETE FROM signals", [])
            .unwrap();
        for threshold in [0, -1, i32::MIN] {
            let settings = Settings {
                focus_score_threshold: threshold,
                ..Settings::default()
            };
            assert!(
                run(&db.0, &[], &settings).is_err(),
                "accepted unsupported threshold {threshold}"
            );
        }
    }
    #[test]
    fn replay_lowest_focus_threshold_suppresses_unknown_scores_without_disabling_volume_entry() {
        let settings = Settings {
            focus_score_threshold: 1,
            ..Settings::default()
        };
        let db = Database::new();
        db.insert(T, "a", 100.0, 30000.0, 50000.0, true);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("DELETE FROM signals", []).unwrap();
        let missing = run(&db.0, &[], &settings).unwrap();
        assert_eq!(missing["coverage"]["signal_missing_groups"], 1);
        assert_eq!(missing["structural_hits"]["A"]["events"], 0);

        let db = Database::new();
        db.insert(T, "a", 100.0, 30000.0, 50000.0, true);
        db.insert(T, "b", 100.0, 30000.0, 40000.0, true);
        let conn = Connection::open(&db.0).unwrap();
        conn.execute("UPDATE signals SET score=0 WHERE pair_address='b'", [])
            .unwrap();
        let conflicting = run(&db.0, &[], &settings).unwrap();
        assert_eq!(conflicting["coverage"]["signal_conflict_groups"], 1);
        assert_eq!(conflicting["structural_hits"]["A"]["events"], 0);
        conn.execute("UPDATE signals SET score=1", []).unwrap();
        assert_eq!(
            run(&db.0, &[], &settings).unwrap()["structural_hits"]["A"]["events"],
            1
        );

        let db = Database::new();
        breakout(&db);
        Connection::open(&db.0)
            .unwrap()
            .execute("DELETE FROM signals", [])
            .unwrap();
        assert_eq!(
            run(&db.0, &[], &settings).unwrap()["structural_hits"]["A"]["events"],
            1
        );
    }
    #[test]
    fn replay_unknown_scores_cannot_rearm_terminated_event_after_recovery() {
        let mut candidates = Vec::new();
        for unknown in ["missing", "malformed", "malformed_score", "conflicting"] {
            let db = Database::new();
            breakout(&db);
            for (offset, price) in [(360, 99.4), (480, 99.3), (600, 99.2), (720, 100.1)] {
                db.insert(
                    T + offset,
                    "a",
                    price,
                    30000.0,
                    70000.0 + offset as f64,
                    true,
                );
            }
            let conn = Connection::open(&db.0).unwrap();
            let at = DateTime::from_timestamp(T + 600, 0).unwrap().to_rfc3339();
            match unknown {
                "missing" => {
                    conn.execute("DELETE FROM signals WHERE observed_at=?1", [&at])
                        .unwrap();
                }
                "malformed_score" => {
                    conn.execute(
                        "UPDATE signals SET score='invalid-score' WHERE observed_at=?1",
                        [&at],
                    )
                    .unwrap();
                }
                "malformed" => {
                    conn.execute(
                        "UPDATE signals SET risk_flags='invalid-json' WHERE observed_at=?1",
                        [&at],
                    )
                    .unwrap();
                }
                _ => {
                    db.insert(T + 600, "b", 99.2, 30000.0, 1.0, true);
                    conn.execute("UPDATE signals SET score=69 WHERE pair_address='b'", [])
                        .unwrap();
                }
            }
            let report = db.replay(false);
            assert_eq!(report["structural_hits"]["B"]["events"], 1, "{unknown}");
            candidates.push((
                unknown,
                report["structural_hits"]["A"]["events"].as_u64().unwrap(),
            ));
            assert_eq!(
                report["coverage"]["accepted_evidence_groups"], 34,
                "{unknown}"
            );
            // A genuine zero must still rearm the score entry; the recovered score is high.
            conn.execute(
                "UPDATE signals SET score=0,risk_flags='[]' WHERE observed_at=?1",
                [&at],
            )
            .unwrap();
            if unknown == "missing" {
                conn.execute("INSERT INTO signals(pair_address,token_address,observed_at,strategy_version,score,pair_state) VALUES ('a','token',?1,'legacy',0,'active')", [&at]).unwrap();
            }
            assert_eq!(
                db.replay(false)["structural_hits"]["A"]["events"],
                2,
                "{unknown}"
            );
        }
        assert_eq!(
            candidates,
            vec![
                ("missing", 1),
                ("malformed", 1),
                ("malformed_score", 1),
                ("conflicting", 1)
            ]
        );
    }

    #[test]
    fn replay_rejects_unknown_arguments_missing_database_and_invalid_settings() {
        let db = Database::new();
        assert!(run(&db.0, &["--anything".into()], &Settings::default()).is_err());
        assert!(run(
            &db.0,
            &["--legacy-proxy".into(), "--legacy-proxy".into()],
            &Settings::default()
        )
        .is_err());
        let missing = format!("{}-does-not-exist/monitor.db", db.0);
        assert!(run(&missing, &[], &Settings::default()).is_err());
        assert!(!std::path::Path::new(&missing).exists());
        assert!(run(
            &db.0,
            &[],
            &Settings {
                min_volume_h1_usd: 0.0,
                ..Settings::default()
            }
        )
        .is_err());
    }
}
