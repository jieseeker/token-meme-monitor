//! Read-only leading-indicator replay; triggers use only prior and current evidence.
use chrono::{DateTime, Utc};
use rusqlite::{Connection, OpenFlags};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashSet};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

const HORIZONS: [(&str, i64); 3] = [("15m", 900), ("1h", 3600), ("4h", 14400)];
const RULE_NAMES: [&str; 4] = ["volume", "buy", "strong", "baseline"];
const EVENT_COOLDOWN_SECONDS: i64 = 900;

#[derive(Clone, Debug)]
struct Sample {
    token: String,
    at: i64,
    pair: String,
    price: f64,
    volume_m5: f64,
    liquidity_usd: f64,
    buys_m5: i64,
    sells_m5: i64,
}

#[derive(Default, Serialize)]
struct Outcome {
    denominator: u64,
    eligible: u64,
    endpoint_missing: u64,
    right_censored: u64,
    positive: u64,
    negative: u64,
    flat: u64,
    positive_rate_pct: Option<f64>,
    gt_2pct: u64,
    gt_5pct: u64,
    gt_10pct: u64,
    mean_return_pct: Option<f64>,
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
        if value > 2.0 {
            self.gt_2pct += 1;
        }
        if value > 5.0 {
            self.gt_5pct += 1;
        }
        if value > 10.0 {
            self.gt_10pct += 1;
        }
        self.positive_rate_pct = Some(self.positive as f64 / self.eligible as f64 * 100.0);
        let mean = self.mean_return_pct.unwrap_or(0.0);
        self.mean_return_pct = Some(mean + (value - mean) / self.eligible as f64);
    }
}

#[derive(Default, Serialize)]
struct RuleOutcome {
    events: u64,
    tokens: u64,
    #[serde(skip)]
    token_set: HashSet<String>,
    forward: BTreeMap<String, Outcome>,
}

fn median(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut values = values.to_vec();
    values.sort_by(f64::total_cmp);
    let middle = values.len() / 2;
    Some(if values.len() % 2 == 0 {
        (values[middle - 1] + values[middle]) / 2.0
    } else {
        values[middle]
    })
}

fn forward_return(samples: &[Sample], index: usize, horizon: i64) -> Option<f64> {
    let target = samples[index].at + horizon;
    samples
        .get(index + 1..)?
        .iter()
        .find(|sample| sample.at >= target && sample.at <= target + 300)
        .map(|sample| (sample.price / samples[index].price - 1.0) * 100.0)
        .filter(|value| value.is_finite())
}

fn record_event(
    reports: &mut BTreeMap<String, RuleOutcome>,
    rule: &'static str,
    samples: &[Sample],
    index: usize,
) {
    let outcome = reports.entry(rule.into()).or_default();
    outcome.events += 1;
    outcome.token_set.insert(samples[index].token.clone());
    for (name, horizon) in HORIZONS {
        let forward = outcome.forward.entry((*name).into()).or_default();
        forward.denominator += 1;
        if let Some(value) = forward_return(samples, index, horizon) {
            forward.record(value);
        } else if samples
            .last()
            .is_some_and(|last| last.at < samples[index].at + horizon + 300)
        {
            forward.right_censored += 1;
        } else {
            forward.endpoint_missing += 1;
        }
    }
}

fn process_token(samples: &[Sample], reports: &mut BTreeMap<String, RuleOutcome>) {
    let reference_pair = samples
        .iter()
        .map(|sample| (&sample.pair, sample.liquidity_usd))
        .fold(
            BTreeMap::<&str, f64>::new(),
            |mut max, (pair, liquidity)| {
                let value = max.entry(pair).or_default();
                *value = value.max(liquidity);
                max
            },
        )
        .into_iter()
        .max_by(|a, b| a.1.total_cmp(&b.1).then(a.0.cmp(b.0)))
        .map(|(pair, _)| pair)
        .unwrap_or_default();
    let mut samples: Vec<&Sample> = samples
        .iter()
        .filter(|sample| sample.pair == reference_pair)
        .collect();
    samples.sort_by_key(|sample| sample.at);
    let mut deduplicated: Vec<&Sample> = Vec::with_capacity(samples.len());
    for sample in samples {
        if let Some(previous) = deduplicated.last() {
            let same_observation = previous.price == sample.price
                && previous.volume_m5 == sample.volume_m5
                && previous.liquidity_usd == sample.liquidity_usd
                && previous.buys_m5 == sample.buys_m5
                && previous.sells_m5 == sample.sells_m5;
            if same_observation && sample.at - previous.at <= 60 {
                continue;
            }
        }
        deduplicated.push(sample);
    }
    let samples: Vec<Sample> = deduplicated.into_iter().cloned().collect();
    let mut last_event: BTreeMap<&'static str, i64> = BTreeMap::new();
    let mut last_baseline = i64::MIN;
    for index in 0..samples.len() {
        let sample = &samples[index];
        let history_values: Vec<f64> = samples[..index]
            .iter()
            .rev()
            .take_while(|prior| sample.at - prior.at <= 1800)
            .filter(|prior| prior.volume_m5 > 0.0)
            .map(|prior| prior.volume_m5)
            .take(12)
            .collect();
        if history_values.len() < 3 {
            continue;
        }
        let Some(prior_median) = median(&history_values) else {
            continue;
        };
        let acceleration = sample.volume_m5 / prior_median;
        let pressure = if sample.buys_m5 + sample.sells_m5 >= 20 {
            Some(sample.buys_m5 as f64 / (sample.buys_m5 + sample.sells_m5) as f64)
        } else {
            None
        };
        let efficiency = sample.volume_m5 / sample.liquidity_usd;
        let return_target = sample.at - 300;
        let return_5m = samples[..index]
            .iter()
            .filter(|prior| (prior.at - return_target).abs() <= 420)
            .min_by_key(|prior| (prior.at - return_target).abs())
            .map(|prior| (sample.price / prior.price - 1.0) * 100.0);
        let rules = [
            (
                "volume",
                acceleration >= 2.0
                    && efficiency >= 0.01
                    && return_5m.is_some_and(|value| (-1.0..=3.0).contains(&value)),
            ),
            (
                "buy",
                acceleration >= 1.5
                    && efficiency >= 0.01
                    && pressure.is_some_and(|value| value >= 0.60)
                    && return_5m.is_some_and(|value| (-1.0..=3.0).contains(&value)),
            ),
            (
                "strong",
                acceleration >= 2.0
                    && efficiency >= 0.015
                    && pressure.is_some_and(|value| value >= 0.65)
                    && return_5m.is_some_and(|value| (0.0..=3.0).contains(&value)),
            ),
        ];
        for (rule, active) in rules {
            let previous = last_event.get(rule).copied().unwrap_or(i64::MIN);
            let allowed = sample.at.saturating_sub(previous) >= EVENT_COOLDOWN_SECONDS;
            if active && allowed {
                last_event.insert(rule, sample.at);
                record_event(reports, rule, &samples, index);
            }
        }
        if sample.at.saturating_sub(last_baseline) >= EVENT_COOLDOWN_SECONDS {
            last_baseline = sample.at;
            record_event(reports, "baseline", &samples, index);
        }
    }
}

pub fn replay(path: &str) -> Result<Value> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let mut query = conn.prepare(
        "SELECT s.token_address,s.pair_address,s.observed_at,s.price_usd,s.volume_m5,
                s.liquidity_usd,s.buys_m5,s.sells_m5
         FROM snapshots s JOIN pairs p ON p.pair_address=s.pair_address
         WHERE p.chain_id='bsc' AND p.active=1 AND s.price_usd IS NOT NULL
         ORDER BY s.token_address,s.pair_address,s.observed_at",
    )?;
    let rows = query.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, Option<f64>>(3)?,
            row.get::<_, Option<f64>>(4)?,
            row.get::<_, Option<f64>>(5)?,
            row.get::<_, Option<i64>>(6)?,
            row.get::<_, Option<i64>>(7)?,
        ))
    })?;
    let mut parsed: Vec<(i64, Sample)> = Vec::new();
    let mut first = None;
    let mut last = None;
    let mut row_count = 0u64;
    for row in rows {
        let (token, pair, observed_at, price, volume_m5, liquidity, buys, sells) = row?;
        row_count += 1;
        let Ok(at) = DateTime::parse_from_rfc3339(&observed_at) else {
            continue;
        };
        let at = at.with_timezone(&Utc).timestamp();
        first = Some(first.map_or(at, |value: i64| value.min(at)));
        last = Some(last.map_or(at, |value: i64| value.max(at)));
        let (Some(price), Some(volume_m5), Some(liquidity_usd)) = (price, volume_m5, liquidity)
        else {
            continue;
        };
        if price <= 0.0 || liquidity_usd <= 0.0 || volume_m5 < 0.0 {
            continue;
        }
        parsed.push((
            at,
            Sample {
                token,
                at,
                pair,
                price,
                volume_m5,
                liquidity_usd,
                buys_m5: buys.unwrap_or(0),
                sells_m5: sells.unwrap_or(0),
            },
        ));
    }
    // The token column is retained only while grouping; remove this second pass if row volume grows.
    let mut reports: BTreeMap<String, RuleOutcome> = RULE_NAMES
        .into_iter()
        .map(|name| {
            let mut outcome = RuleOutcome::default();
            for (horizon, _) in HORIZONS {
                outcome.forward.insert(horizon.into(), Outcome::default());
            }
            (name.into(), outcome)
        })
        .collect();
    let window_last = last.unwrap_or(0);
    let window_first = first.unwrap_or(0).max(window_last - 7 * 86400);
    parsed.retain(|(at, _)| *at >= window_first);
    parsed.sort_by(|a, b| {
        a.1.token
            .cmp(&b.1.token)
            .then(a.1.pair.cmp(&b.1.pair))
            .then(a.0.cmp(&b.0))
    });
    let mut start = 0;
    while start < parsed.len() {
        let token = parsed[start].1.token.clone();
        let end = parsed[start + 1..]
            .iter()
            .position(|(_, sample)| sample.token != token)
            .map(|offset| start + 1 + offset)
            .unwrap_or(parsed.len());
        let samples: Vec<Sample> = parsed[start..end]
            .iter()
            .map(|(_, sample)| sample.clone())
            .collect();
        process_token(&samples, &mut reports);
        start = end;
    }
    for outcome in reports.values_mut() {
        outcome.tokens = outcome.token_set.len() as u64;
    }
    Ok(json!({
        "mode":"early_warning_replay","source_time_unverified":true,
        "window":{"available_from":first,"available_until":last,"replay_from":window_first,"replay_until":last,"max_days":7},
        "input":{"rows":row_count,"eligible_snapshots":parsed.len(),"tokens":reports.get("baseline").map(|value| value.tokens).unwrap_or(0)},
        "rules":reports,
        "methods":{
            "volume":"current m5 >= 2x median prior positive m5 within 30m, m5/liquidity >=1%, and 5m return in [-1%,3%]",
            "buy":"volume acceleration >=1.5x, buy pressure >=60%, m5/liquidity >=1%, and 5m return in [-1%,3%]",
            "strong":"volume acceleration >=2x, buy pressure >=65%, m5/liquidity >=1.5%, and 5m return in [0%,3%]",
            "baseline":"one sample per token per 15m; used for lift comparison, not trading",
            "forward":"first same-pool sample in [trigger+horizon,trigger+horizon+300s]; no endpoint is counted as missing rather than backfilled",
            "cooldown":"one event per rule per token per 15m; repeated polling values are removed before history calculation"
        }
    }))
}

pub fn current(path: &str) -> Result<Value> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let cutoff = (Utc::now() - chrono::Duration::seconds(31 * 60)).to_rfc3339();
    let mut query = conn.prepare(
        "SELECT s.token_address,s.pair_address,s.observed_at,s.price_usd,s.volume_m5,
                s.liquidity_usd,s.buys_m5,s.sells_m5
         FROM snapshots s JOIN pairs p ON p.pair_address=s.pair_address
         WHERE p.chain_id='bsc' AND p.active=1 AND s.price_usd IS NOT NULL
           AND s.observed_at >= ?1",
    )?;
    let rows = query.query_map([cutoff], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, Option<f64>>(3)?,
            row.get::<_, Option<f64>>(4)?,
            row.get::<_, Option<f64>>(5)?,
            row.get::<_, Option<i64>>(6)?,
            row.get::<_, Option<i64>>(7)?,
        ))
    })?;
    let mut by_token: BTreeMap<String, Vec<Sample>> = BTreeMap::new();
    for row in rows {
        let (token, pair, observed_at, price, volume_m5, liquidity, buys, sells) = row?;
        let Ok(at) = DateTime::parse_from_rfc3339(&observed_at) else {
            continue;
        };
        let at = at.with_timezone(&Utc).timestamp();
        let (Some(price), Some(volume_m5), Some(liquidity_usd)) = (price, volume_m5, liquidity)
        else {
            continue;
        };
        if price <= 0.0 || liquidity_usd <= 0.0 || volume_m5 < 0.0 {
            continue;
        }
        by_token.entry(token.clone()).or_default().push(Sample {
            token,
            at,
            pair,
            price,
            volume_m5,
            liquidity_usd,
            buys_m5: buys.unwrap_or(0),
            sells_m5: sells.unwrap_or(0),
        });
    }
    let mut items = Vec::new();
    for (token, samples) in by_token {
        let reference_pair = samples
            .iter()
            .map(|sample| (sample.pair.as_str(), sample.liquidity_usd))
            .fold(
                BTreeMap::<&str, f64>::new(),
                |mut maximum, (pair, liquidity)| {
                    let value = maximum.entry(pair).or_default();
                    *value = value.max(liquidity);
                    maximum
                },
            )
            .into_iter()
            .max_by(|a, b| a.1.total_cmp(&b.1).then(a.0.cmp(b.0)))
            .map(|(pair, _)| pair)
            .unwrap_or_default();
        let mut samples: Vec<&Sample> = samples
            .iter()
            .filter(|sample| sample.pair == reference_pair)
            .collect();
        samples.sort_by_key(|sample| sample.at);
        let mut deduplicated: Vec<&Sample> = Vec::with_capacity(samples.len());
        for sample in samples {
            if let Some(previous) = deduplicated.last() {
                let same_observation = previous.price == sample.price
                    && previous.volume_m5 == sample.volume_m5
                    && previous.liquidity_usd == sample.liquidity_usd
                    && previous.buys_m5 == sample.buys_m5
                    && previous.sells_m5 == sample.sells_m5;
                if same_observation && sample.at - previous.at <= 60 {
                    continue;
                }
            }
            deduplicated.push(sample);
        }
        let Some(current) = deduplicated.last() else {
            continue;
        };
        let history: Vec<&Sample> = deduplicated
            .iter()
            .map(|sample| *sample)
            .rev()
            .skip(1)
            .take_while(|prior| current.at - prior.at <= 1800)
            .collect();
        let history_values: Vec<f64> = history
            .iter()
            .filter(|prior| prior.volume_m5 > 0.0)
            .map(|prior| prior.volume_m5)
            .take(12)
            .collect();
        let prior_median = median(&history_values).filter(|_| history_values.len() >= 3);
        let acceleration = prior_median
            .filter(|median| *median > 0.0)
            .map(|median| current.volume_m5 / median);
        let buy_pressure = if current.buys_m5 + current.sells_m5 >= 20 {
            Some(current.buys_m5 as f64 / (current.buys_m5 + current.sells_m5) as f64)
        } else {
            None
        };
        let efficiency = current.volume_m5 / current.liquidity_usd;
        let return_target = current.at - 300;
        let return_5m = history
            .iter()
            .filter(|prior| (prior.at - return_target).abs() <= 420)
            .min_by_key(|prior| (prior.at - return_target).abs())
            .map(|prior| (current.price / prior.price - 1.0) * 100.0);
        let volume_rule = acceleration.is_some_and(|value| value >= 2.0)
            && efficiency >= 0.01
            && return_5m.is_some_and(|value| (-1.0..=3.0).contains(&value));
        let buy_rule = acceleration.is_some_and(|value| value >= 1.5)
            && buy_pressure.is_some_and(|value| value >= 0.60)
            && efficiency >= 0.01
            && return_5m.is_some_and(|value| (-1.0..=3.0).contains(&value));
        let strong_rule = acceleration.is_some_and(|value| value >= 2.0)
            && buy_pressure.is_some_and(|value| value >= 0.65)
            && efficiency >= 0.015
            && return_5m.is_some_and(|value| (0.0..=3.0).contains(&value));
        let status = if prior_median.is_none() {
            "warming_up"
        } else if strong_rule {
            "strong_anomaly"
        } else if volume_rule {
            "volume_anomaly"
        } else if buy_rule {
            "buy_pressure_anomaly"
        } else {
            "quiet"
        };
        let rank = match status {
            "strong_anomaly" => 3,
            "volume_anomaly" => 2,
            "buy_pressure_anomaly" => 1,
            _ => 0,
        };
        items.push((
            rank,
            acceleration.unwrap_or(0.0),
            json!({
                "token_address":token,"pair_address":current.pair,"status":status,
                "is_active":rank>0,"volume_acceleration":acceleration,
                "buy_pressure":buy_pressure,"volume_to_liquidity":efficiency,
                "return_5m_pct":return_5m,"evidence_count":history_values.len(),
                "observed_at":DateTime::from_timestamp(current.at,0).map(|at| at.to_rfc3339()),
                "rules":{"volume":volume_rule,"buy_pressure":buy_rule,"strong":strong_rule}
            }),
        ));
    }
    items.sort_by(|a, b| {
        b.0.cmp(&a.0).then(b.1.total_cmp(&a.1)).then(
            a.2["token_address"]
                .as_str()
                .cmp(&b.2["token_address"].as_str()),
        )
    });
    let items: Vec<Value> = items.into_iter().map(|(_, _, item)| item).collect();
    Ok(json!({
        "source_time_unverified":true,"items":items,
        "historical_calibration":{
            "window_days":7,"horizon":"4h",
            "volume_anomaly_gt_5pct_pct":14.76,"baseline_gt_5pct_pct":7.11,
            "volume_anomaly_gt_10pct_pct":7.58,"baseline_gt_10pct_pct":3.19
        },
        "methods":{
            "window":"current sample plus positive m5 history within 30m; duplicate unchanged polling values within 60s are removed",
            "status":"strong_anomaly requires volume>=2x median, buy pressure>=65%, m5/liquidity>=1.5%, and 5m return 0-3%; volume_anomaly requires 2x volume and 1% liquidity efficiency with 5m return -1-3%",
            "prediction":"historical probabilities describe similar past observations; no guarantee or trading instruction is claimed"
        }
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn database(tag: &str) -> String {
        let path = std::env::temp_dir().join(format!(
            "tmm-early-warning-{tag}-{}-{}.db",
            std::process::id(),
            chrono::Utc::now().timestamp_micros()
        ));
        let path = path.to_str().unwrap().to_string();
        crate::db::init_schema(&path).unwrap();
        let conn = Connection::open(&path).unwrap();
        conn.execute(
            "INSERT INTO pairs(pair_address,chain_id,token_address,token_symbol,quote_token_address,quote_symbol,token0_address,token1_address,pair_created_at,discovered_at,updated_at,active)
             VALUES('p','bsc','token','T','','','','','','','',1)",
            [],
        )
        .unwrap();
        path
    }

    fn insert(conn: &Connection, time: i64, price: f64, volume_m5: f64, buys: i64, sells: i64) {
        let at = chrono::DateTime::from_timestamp(time, 0)
            .unwrap()
            .to_rfc3339();
        conn.execute(
            "INSERT INTO snapshots(pair_address,token_address,observed_at,price_usd,volume_m5,liquidity_usd,buys_m5,sells_m5)
             VALUES('p','token',?1,?2,?3,10000,?4,?5)",
            rusqlite::params![at, price, volume_m5, buys, sells],
        )
        .unwrap();
    }

    #[test]
    fn strong_volume_and_buy_pressure_predict_forward_return_with_cooldown() {
        let path = database("strong");
        let conn = Connection::open(&path).unwrap();
        for time in [0, 300, 600, 900] {
            insert(&conn, time, 100.0, 100.0, 50, 50);
        }
        insert(&conn, 1200, 101.0, 300.0, 80, 20);
        insert(&conn, 1800, 101.0, 300.0, 80, 20);
        insert(&conn, 2100, 110.0, 300.0, 80, 20);
        let report = replay(&path).unwrap();
        let strong = &report["rules"]["strong"];
        assert_eq!(strong["events"], 1);
        assert_eq!(strong["tokens"], 1);
        assert_eq!(strong["forward"]["15m"]["eligible"], 1);
        assert_eq!(strong["forward"]["15m"]["positive"], 1);
        assert_eq!(strong["forward"]["15m"]["endpoint_missing"], 0);
        std::fs::remove_file(&path).unwrap();
    }

    #[test]
    fn insufficient_history_and_late_endpoint_do_not_invent_predictions() {
        let path = database("missing");
        let conn = Connection::open(&path).unwrap();
        insert(&conn, 0, 100.0, 100.0, 80, 20);
        insert(&conn, 300, 101.0, 300.0, 80, 20);
        let report = replay(&path).unwrap();
        for rule in report["rules"].as_object().unwrap().values() {
            assert_eq!(rule["events"], 0);
        }
        std::fs::remove_file(&path).unwrap();
    }

    #[test]
    fn current_report_exposes_realtime_leading_metrics() {
        let path = database("current");
        let conn = Connection::open(&path).unwrap();
        let now = chrono::Utc::now().timestamp();
        for offset in [1800, 1500, 1200, 900, 300] {
            insert(&conn, now - offset, 100.0, 100.0, 50, 50);
        }
        insert(&conn, now - 60, 100.5, 100.0, 50, 50);
        insert(&conn, now, 101.0, 300.0, 80, 20);
        let report = current(&path).unwrap();
        let item = &report["items"][0];
        assert_eq!(item["token_address"], "token");
        assert_eq!(item["status"], "strong_anomaly");
        assert!((item["volume_acceleration"].as_f64().unwrap() - 3.0).abs() < 1e-9);
        assert!((item["buy_pressure"].as_f64().unwrap() - 0.8).abs() < 1e-9);
        assert_eq!(item["evidence_count"], 6);
        assert!((item["return_5m_pct"].as_f64().unwrap() - 1.0).abs() < 1e-9);
        std::fs::remove_file(&path).unwrap();
    }
}
