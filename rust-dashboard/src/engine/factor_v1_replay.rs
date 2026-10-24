//! Read-only factor replay. This is a research tool, not a live signal.
use chrono::{DateTime, Utc};
use rusqlite::{Connection, OpenFlags};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Clone, Debug)]
struct Sample {
    at: i64,
    token: String,
    pair: String,
    price: f64,
    volume_m5: f64,
    liquidity_usd: f64,
    buys_m5: i64,
    sells_m5: i64,
}

#[derive(Clone, Debug, Serialize)]
struct FactorRow {
    token: String,
    pair: String,
    at: i64,
    momentum_5m: Option<f64>,
    momentum_15m: Option<f64>,
    breakout_30m: Option<f64>,
    relative_volume_30m: Option<f64>,
    buy_pressure_5m: Option<f64>,
    liquidity_stability_30m: Option<f64>,
    forward_15m: Option<f64>,
    forward_1h: Option<f64>,
}

#[derive(Default, Serialize)]
struct FactorStats {
    count: usize,
    mean: Option<f64>,
    median: Option<f64>,
    p10: Option<f64>,
    p90: Option<f64>,
    correlation_15m: Option<f64>,
    correlation_1h: Option<f64>,
}

#[derive(Default, Serialize)]
struct Coverage {
    rows: usize,
    tokens: usize,
    evaluated_tokens: usize,
    skipped_tokens_no_history: usize,
    factor_rows: usize,
    forward_15m_rows: usize,
    forward_1h_rows: usize,
}

pub fn replay(path: &str) -> Result<Value> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let mut stmt = conn.prepare(
        "SELECT token_address,pair_address,observed_at,price_usd,volume_m5,liquidity_usd,buys_m5,sells_m5
         FROM snapshots
         ORDER BY token_address,julianday(observed_at),pair_address",
    )?;
    let rows = stmt.query_map([], |row| {
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

    let mut grouped: HashMap<String, Vec<Sample>> = HashMap::new();
    let mut pair_liquidity: HashMap<(String, String), f64> = HashMap::new();
    let mut row_count = 0usize;
    for row in rows {
        let (token, pair, observed_at, price, volume_m5, liquidity_usd, buys_m5, sells_m5) = row?;
        row_count += 1;
        let Ok(parsed) = DateTime::parse_from_rfc3339(&observed_at) else {
            continue;
        };
        let at = parsed.with_timezone(&Utc).timestamp();
        let Some(price) = price.filter(|value| value.is_finite() && *value > 0.0) else {
            continue;
        };
        let liquidity_usd = liquidity_usd.filter(|value| value.is_finite() && *value >= 0.0);
        let volume_m5 = volume_m5.filter(|value| value.is_finite() && *value >= 0.0);
        let liquidity_value = liquidity_usd.unwrap_or(0.0);
        *pair_liquidity
            .entry((token.clone(), pair.clone()))
            .or_insert(0.0) = (*pair_liquidity
            .get(&(token.clone(), pair.clone()))
            .unwrap_or(&0.0))
        .max(liquidity_value);
        grouped.entry(token.clone()).or_default().push(Sample {
            at,
            token: token.clone(),
            pair: pair.clone(),
            price,
            volume_m5: volume_m5.unwrap_or(0.0),
            liquidity_usd: liquidity_value,
            buys_m5: buys_m5.unwrap_or(0),
            sells_m5: sells_m5.unwrap_or(0),
        });
    }

    let mut coverage = Coverage {
        rows: row_count,
        tokens: grouped.len(),
        ..Coverage::default()
    };
    let mut factors: Vec<FactorRow> = Vec::new();
    for (token, samples) in grouped {
        let Some(pair) = pair_liquidity
            .iter()
            .filter(|((candidate_token, _), _)| candidate_token == &token)
            .max_by(|a, b| a.1.total_cmp(b.1).then(a.0.cmp(b.0)))
            .map(|((_, pair), _)| pair.clone())
        else {
            continue;
        };
        let mut samples: Vec<Sample> = samples
            .into_iter()
            .filter(|sample| sample.pair == pair)
            .collect();
        samples.sort_by_key(|sample| sample.at);
        if samples.len() < 6 || samples.last().unwrap().at - samples.first().unwrap().at < 1_500 {
            coverage.skipped_tokens_no_history += 1;
            continue;
        }
        coverage.evaluated_tokens += 1;
        for (index, sample) in samples.iter().enumerate() {
            let history = &samples[..index];
            let momentum_5m = nearest_prior(history, sample.at - 300, 300)
                .map(|prior| sample.price / prior.price - 1.0);
            let momentum_15m = nearest_prior(history, sample.at - 900, 600)
                .map(|prior| sample.price / prior.price - 1.0);
            let recent: Vec<&Sample> = history
                .iter()
                .rev()
                .take_while(|prior| sample.at - prior.at <= 1_800)
                .collect();
            let breakout_30m = (!recent.is_empty()).then(|| {
                sample.price
                    / recent
                        .iter()
                        .map(|prior| prior.price)
                        .fold(f64::MIN, f64::max)
                    - 1.0
            });
            let volume_values: Vec<f64> = recent.iter().map(|prior| prior.volume_m5).collect();
            let volume_median = median(&volume_values);
            let relative_volume_30m = volume_median
                .filter(|value| *value >= 1.0)
                .map(|value| sample.volume_m5 / value);
            let buy_pressure_5m = (sample.buys_m5 + sample.sells_m5 > 0)
                .then(|| sample.buys_m5 as f64 / (sample.buys_m5 + sample.sells_m5) as f64);
            let liquidity_values: Vec<f64> =
                recent.iter().map(|prior| prior.liquidity_usd).collect();
            let liquidity_stability_30m = median(&liquidity_values)
                .filter(|value| *value > 0.0)
                .map(|value| sample.liquidity_usd / value);
            let forward_15m = forward_return(&samples, index, 900);
            let forward_1h = forward_return(&samples, index, 3_600);
            if momentum_5m.is_some()
                || momentum_15m.is_some()
                || breakout_30m.is_some()
                || relative_volume_30m.is_some()
                || buy_pressure_5m.is_some()
                || liquidity_stability_30m.is_some()
            {
                coverage.factor_rows += 1;
            }
            if forward_15m.is_some() {
                coverage.forward_15m_rows += 1;
            }
            if forward_1h.is_some() {
                coverage.forward_1h_rows += 1;
            }
            factors.push(FactorRow {
                token: sample.token.clone(),
                pair: sample.pair.clone(),
                at: sample.at,
                momentum_5m,
                momentum_15m,
                breakout_30m,
                relative_volume_30m,
                buy_pressure_5m,
                liquidity_stability_30m,
                forward_15m,
                forward_1h,
            });
        }
    }

    let factor_stats = factor_statistics(&factors);
    let mut strong_breakouts: Vec<&FactorRow> = factors
        .iter()
        .filter(|row| row.breakout_30m.is_some_and(|value| value > 0.02))
        .collect();
    strong_breakouts.sort_by(|a, b| b.breakout_30m.unwrap().total_cmp(&a.breakout_30m.unwrap()));
    strong_breakouts.truncate(20);
    let mut strong_volume: Vec<&FactorRow> = factors
        .iter()
        .filter(|row| row.relative_volume_30m.is_some_and(|value| value > 1.5))
        .collect();
    strong_volume.sort_by(|a, b| {
        b.relative_volume_30m
            .unwrap()
            .total_cmp(&a.relative_volume_30m.unwrap())
    });
    strong_volume.truncate(20);

    Ok(json!({
        "experiment": "factor_v1_replay",
        "definitions": {
            "momentum_5m": "5 分钟价格变化",
            "momentum_15m": "15 分钟价格变化",
            "breakout_30m": "相对过去 30 分钟最高价",
            "relative_volume_30m": "当前 5 分钟成交量 / 过去 30 分钟成交量中位数",
            "buy_pressure_5m": "5 分钟买入占比",
            "liquidity_stability_30m": "当前流动性 / 过去 30 分钟流动性中位数"
        },
        "coverage": coverage,
        "factor_stats": factor_stats,
        "strong_breakouts": strong_breakouts,
        "strong_volume_spikes": strong_volume
    }))
}

fn nearest_prior<'a>(samples: &'a [Sample], target: i64, tolerance: i64) -> Option<&'a Sample> {
    samples
        .iter()
        .rev()
        .take_while(|sample| sample.at <= target + tolerance)
        .find(|sample| sample.at >= target - tolerance)
}

fn forward_return(samples: &[Sample], index: usize, horizon: i64) -> Option<f64> {
    let sample = samples.get(index)?;
    let target = sample.at + horizon;
    samples
        .get(index + 1..)?
        .iter()
        .find(|future| future.at >= target && future.at <= target + 300)
        .map(|future| future.price / sample.price - 1.0)
}

fn median(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut values = values.to_vec();
    values.sort_by(|a, b| a.total_cmp(b));
    let middle = values.len() / 2;
    Some(if values.len() % 2 == 0 {
        (values[middle - 1] + values[middle]) / 2.0
    } else {
        values[middle]
    })
}

fn percentile(values: &[f64], percentile: f64) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut values = values.to_vec();
    values.sort_by(|a, b| a.total_cmp(b));
    let index = ((values.len() - 1) as f64 * percentile).round() as usize;
    values.get(index).copied()
}

fn correlation(pairs: &[(f64, f64)]) -> Option<f64> {
    if pairs.len() < 3 {
        return None;
    }
    let mean_x = pairs.iter().map(|(x, _)| *x).sum::<f64>() / pairs.len() as f64;
    let mean_y = pairs.iter().map(|(_, y)| *y).sum::<f64>() / pairs.len() as f64;
    let mut numerator = 0.0;
    let mut denominator_x = 0.0;
    let mut denominator_y = 0.0;
    for (x, y) in pairs {
        let dx = x - mean_x;
        let dy = y - mean_y;
        numerator += dx * dy;
        denominator_x += dx * dx;
        denominator_y += dy * dy;
    }
    if denominator_x == 0.0 || denominator_y == 0.0 {
        return None;
    }
    Some(numerator / (denominator_x * denominator_y).sqrt())
}

fn factor_statistics(factors: &[FactorRow]) -> BTreeMap<String, FactorStats> {
    let mut result = BTreeMap::new();
    let mut add = |name: &str, values: Vec<Option<f64>>| {
        let present: Vec<f64> = values.iter().flatten().copied().collect();
        let mut stats = FactorStats {
            count: present.len(),
            mean: (!present.is_empty()).then(|| present.iter().sum::<f64>() / present.len() as f64),
            median: median(&present),
            p10: percentile(&present, 0.10),
            p90: percentile(&present, 0.90),
            correlation_15m: None,
            correlation_1h: None,
        };
        if present.len() >= 3 {
            let pairs_15m: Vec<(f64, f64)> = factors
                .iter()
                .zip(values.iter())
                .filter_map(|(row, value)| value.zip(row.forward_15m))
                .collect();
            let pairs_1h: Vec<(f64, f64)> = factors
                .iter()
                .zip(values.iter())
                .filter_map(|(row, value)| value.zip(row.forward_1h))
                .collect();
            stats.correlation_15m = correlation(&pairs_15m);
            stats.correlation_1h = correlation(&pairs_1h);
        }
        result.insert(name.to_string(), stats);
    };
    add(
        "momentum_5m",
        factors.iter().map(|row| row.momentum_5m).collect(),
    );
    add(
        "momentum_15m",
        factors.iter().map(|row| row.momentum_15m).collect(),
    );
    add(
        "breakout_30m",
        factors.iter().map(|row| row.breakout_30m).collect(),
    );
    add(
        "relative_volume_30m",
        factors.iter().map(|row| row.relative_volume_30m).collect(),
    );
    add(
        "buy_pressure_5m",
        factors.iter().map(|row| row.buy_pressure_5m).collect(),
    );
    add(
        "liquidity_stability_30m",
        factors
            .iter()
            .map(|row| row.liquidity_stability_30m)
            .collect(),
    );
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn median_and_percentile_are_stable() {
        assert_eq!(median(&[3.0, 1.0, 2.0]), Some(2.0));
        assert_eq!(median(&[4.0, 1.0, 3.0, 2.0]), Some(2.5));
        assert_eq!(percentile(&[1.0, 2.0, 3.0, 4.0], 0.25), Some(2.0));
    }

    #[test]
    fn correlation_detects_linear_relationship() {
        let pairs = vec![(1.0, 2.0), (2.0, 4.0), (3.0, 6.0)];
        let value = correlation(&pairs).unwrap();
        assert!((value - 1.0).abs() < 1e-9);
    }
}
