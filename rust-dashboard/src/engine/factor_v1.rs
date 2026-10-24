//! Live structure factors for the dashboard. Values are research-only.
use chrono::{DateTime, Utc};
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Clone, Debug)]
struct Snapshot {
    at: i64,
    pair: String,
    price: f64,
    volume_m5: f64,
    liquidity_usd: f64,
    buys_m5: i64,
    sells_m5: i64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum FactorStatus {
    Strong,
    Neutral,
    Weak,
}

impl FactorStatus {
    fn label(self) -> &'static str {
        match self {
            Self::Strong => "强",
            Self::Neutral => "中性",
            Self::Weak => "弱",
        }
    }

    fn key(self) -> &'static str {
        match self {
            Self::Strong => "strong",
            Self::Neutral => "neutral",
            Self::Weak => "weak",
        }
    }
}

const STAGE_ORDER: [&str; 9] = [
    "inactive",
    "watch",
    "first_wave",
    "pullback",
    "base",
    "first_start",
    "first_start_extended",
    "second_start",
    "second_start_extended",
];

pub fn stage_label(stage: &str) -> &'static str {
    match stage {
        "inactive" => "未启动",
        "watch" => "观察",
        "first_wave" => "第一波",
        "pullback" => "回调",
        "base" => "企稳",
        "first_start" => "首次启动",
        "first_start_extended" => "首次启动 · 超8%",
        "second_start" => "二次启动",
        "second_start_extended" => "二次启动 · 超8%",
        _ => "未知",
    }
}

pub fn data_status_label(status: &str) -> &'static str {
    match status {
        "current" => "数据连续",
        "warming_up" => "数据预热中",
        "interrupted" => "数据断档",
        "expired" => "窗口过期",
        "source_changed" => "数据源变更",
        "invalid_input" => "数据无效",
        "missing_price" | "missing_m5" | "missing_h1" | "missing_liquidity" => "数据缺失",
        _ => "数据状态未知",
    }
}

pub fn risk_status_label(status: &str) -> &'static str {
    match status {
        "PASS" => "风险通过",
        "WARN" => "风险提示",
        "BLOCK" => "风险阻断",
        _ => "风险未知",
    }
}

fn percent(value: f64) -> String {
    format!(
        "{}{:.1}%",
        if value > 0.0 { "+" } else { "" },
        value * 100.0
    )
}

fn ratio(value: f64) -> String {
    format!("{:.2}x", value)
}

fn factor_status(value: f64, kind: &str) -> FactorStatus {
    match kind {
        "momentum_5m" | "momentum_15m" | "breakout_30m" => {
            if value > 0.02 {
                FactorStatus::Strong
            } else if value < -0.02 {
                FactorStatus::Weak
            } else {
                FactorStatus::Neutral
            }
        }
        "relative_volume_30m" => {
            if value > 1.5 {
                FactorStatus::Strong
            } else if value < 0.5 {
                FactorStatus::Weak
            } else {
                FactorStatus::Neutral
            }
        }
        "buy_pressure_5m" => {
            if value > 0.55 {
                FactorStatus::Strong
            } else if value < 0.45 {
                FactorStatus::Weak
            } else {
                FactorStatus::Neutral
            }
        }
        "liquidity_stability_30m" => {
            if value > 1.02 {
                FactorStatus::Strong
            } else if value < 0.98 {
                FactorStatus::Weak
            } else {
                FactorStatus::Neutral
            }
        }
        _ => FactorStatus::Neutral,
    }
}

fn factor_json(value: Option<f64>, kind: &str, help: &str) -> Value {
    let Some(value) = value.filter(|value| value.is_finite()) else {
        return json!({"value": null, "display": "--", "status": "neutral", "status_label": "中性", "help": help});
    };
    let status = factor_status(value, kind);
    let display = match kind {
        "relative_volume_30m" => ratio(value),
        "buy_pressure_5m" => format!("{:.1}%", value * 100.0),
        "liquidity_stability_30m" => format!("{:.2}", value),
        _ => percent(value),
    };
    json!({
        "value": value,
        "display": display,
        "status": status.key(),
        "status_label": status.label(),
        "help": help
    })
}

fn structure_status(factors: &Value) -> (&'static str, &'static str) {
    let statuses: Vec<&str> = [
        "momentum_5m",
        "momentum_15m",
        "breakout_30m",
        "relative_volume_30m",
        "buy_pressure_5m",
        "liquidity_stability_30m",
    ]
    .iter()
    .map(|key| factors[*key]["status"].as_str().unwrap_or("neutral"))
    .collect();
    let strong = statuses
        .iter()
        .filter(|status| **status == "strong")
        .count();
    let weak = statuses.iter().filter(|status| **status == "weak").count();
    if strong >= 4 && weak == 0 {
        ("strong", "结构良好")
    } else if weak >= 3 || (weak >= 2 && statuses[..3].iter().any(|status| *status == "weak")) {
        ("weak", "结构转弱")
    } else {
        ("neutral", "结构观察")
    }
}

fn conclusion(stage: &str, status: &str, factors: &Value) -> String {
    if stage == "inactive" {
        return "尚未进入结构观察窗口".to_string();
    }
    if factors["momentum_5m"]["status"] == "weak"
        && factors["relative_volume_30m"]["status"] == "strong"
    {
        return "放量回落：成交活跃，但短期价格走弱".to_string();
    }
    if factors["buy_pressure_5m"]["status"] == "weak" && factors["breakout_30m"]["status"] == "weak"
    {
        return "卖压偏重：买入占比不足，价格低于近期高点".to_string();
    }
    if status == "strong" {
        return "结构良好：价格、量能与买入占比同步转强".to_string();
    }
    if status == "weak" {
        return "结构转弱：多项因子走弱，暂不视为有效突破".to_string();
    }
    "结构观察：因子尚未形成一致方向".to_string()
}

fn stage_timeline(stage: &str) -> Value {
    json!(STAGE_ORDER
        .iter()
        .map(|name| json!({
            "stage": name,
            "label": stage_label(name),
            "active": *name == stage
        }))
        .collect::<Vec<_>>())
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

fn nearest_prior(samples: &[Snapshot], target: i64, tolerance: i64) -> Option<&Snapshot> {
    samples
        .iter()
        .rev()
        .take_while(|sample| sample.at <= target + tolerance)
        .find(|sample| sample.at >= target - tolerance)
}

fn read_snapshots(conn: &Connection, token: &str) -> Result<Vec<Snapshot>> {
    let mut stmt = conn.prepare(
        "SELECT pair_address,observed_at,price_usd,volume_m5,liquidity_usd,buys_m5,sells_m5
         FROM snapshots
         WHERE token_address=?1
         ORDER BY observed_at DESC
         LIMIT 2000",
    )?;
    let rows = stmt.query_map([token], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, Option<f64>>(2)?,
            row.get::<_, Option<f64>>(3)?,
            row.get::<_, Option<f64>>(4)?,
            row.get::<_, Option<i64>>(5)?,
            row.get::<_, Option<i64>>(6)?,
        ))
    })?;
    let mut snapshots = Vec::new();
    for row in rows {
        let (pair, observed_at, price, volume_m5, liquidity_usd, buys_m5, sells_m5) = row?;
        let Ok(parsed) = DateTime::parse_from_rfc3339(&observed_at) else {
            continue;
        };
        let Some(price) = price.filter(|value| value.is_finite() && *value > 0.0) else {
            continue;
        };
        snapshots.push(Snapshot {
            at: parsed.with_timezone(&Utc).timestamp(),
            pair,
            price,
            volume_m5: volume_m5
                .filter(|value| value.is_finite() && *value >= 0.0)
                .unwrap_or(0.0),
            liquidity_usd: liquidity_usd
                .filter(|value| value.is_finite() && *value >= 0.0)
                .unwrap_or(0.0),
            buys_m5: buys_m5.unwrap_or(0),
            sells_m5: sells_m5.unwrap_or(0),
        });
    }
    Ok(snapshots)
}

fn read_stage(conn: &Connection, token: &str) -> Value {
    conn.query_row(
        "SELECT summary_json FROM signal_v2_state WHERE token_address=?1",
        [token],
        |row| row.get::<_, String>(0),
    )
    .ok()
    .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
    .unwrap_or(Value::Null)
}

pub fn structure_for_token(conn: &Connection, token: &str) -> Value {
    let token = token.to_ascii_lowercase();
    let snapshots = read_snapshots(conn, &token).unwrap_or_default();
    let stage = read_stage(conn, &token);
    let stage_name = stage["stage"].as_str().unwrap_or("inactive").to_string();
    let data_status = stage["data_status"]
        .as_str()
        .unwrap_or("unknown")
        .to_string();
    let risk_status = stage["risk_status"]
        .as_str()
        .unwrap_or("unknown")
        .to_string();

    let Some(latest_at) = snapshots.iter().map(|sample| sample.at).max() else {
        return unavailable(&token, stage_name, data_status, risk_status, "暂无可用快照");
    };
    let window: Vec<Snapshot> = snapshots
        .into_iter()
        .filter(|sample| latest_at - sample.at <= 3_600)
        .collect();
    let Some(pair) = window
        .iter()
        .map(|sample| (&sample.pair, sample.liquidity_usd))
        .max_by(|a, b| a.1.total_cmp(&b.1).then(a.0.cmp(b.0)))
        .map(|(pair, _)| pair.clone())
    else {
        return unavailable(&token, stage_name, data_status, risk_status, "暂无可用池子");
    };
    let mut samples: Vec<Snapshot> = window
        .into_iter()
        .filter(|sample| sample.pair == pair)
        .collect();
    samples.sort_by_key(|sample| sample.at);
    if samples.len() < 6 || samples.last().unwrap().at - samples.first().unwrap().at < 1_500 {
        return unavailable(&token, stage_name, data_status, risk_status, "历史样本不足");
    }

    let latest = samples.last().unwrap();
    let history = &samples[..samples.len() - 1];
    let momentum_5m =
        nearest_prior(history, latest.at - 300, 300).map(|prior| latest.price / prior.price - 1.0);
    let momentum_15m =
        nearest_prior(history, latest.at - 900, 600).map(|prior| latest.price / prior.price - 1.0);
    let recent: Vec<&Snapshot> = history
        .iter()
        .rev()
        .take_while(|prior| latest.at - prior.at <= 1_800)
        .collect();
    let breakout_30m = (!recent.is_empty()).then(|| {
        latest.price
            / recent
                .iter()
                .map(|prior| prior.price)
                .fold(f64::MIN, f64::max)
            - 1.0
    });
    let volume_values: Vec<f64> = recent.iter().map(|prior| prior.volume_m5).collect();
    let relative_volume_30m = median(&volume_values)
        .filter(|value| *value >= 1.0)
        .map(|value| latest.volume_m5 / value);
    let buy_pressure_5m = (latest.buys_m5 + latest.sells_m5 > 0)
        .then(|| latest.buys_m5 as f64 / (latest.buys_m5 + latest.sells_m5) as f64);
    let liquidity_values: Vec<f64> = recent.iter().map(|prior| prior.liquidity_usd).collect();
    let liquidity_stability_30m = median(&liquidity_values)
        .filter(|value| *value > 0.0)
        .map(|value| latest.liquidity_usd / value);

    let factors = json!({
        "momentum_5m": factor_json(momentum_5m, "momentum_5m", "最近 5 分钟价格变化；正数代表上涨，负数代表下跌。"),
        "momentum_15m": factor_json(momentum_15m, "momentum_15m", "最近 15 分钟价格变化；用于观察短期趋势是否延续。"),
        "breakout_30m": factor_json(breakout_30m, "breakout_30m", "当前价格相对过去 30 分钟最高价的位置；正数代表突破，负数代表回落。"),
        "relative_volume_30m": factor_json(relative_volume_30m, "relative_volume_30m", "当前 5 分钟成交额除以过去 30 分钟成交额中位数；用于识别放量。"),
        "buy_pressure_5m": factor_json(buy_pressure_5m, "buy_pressure_5m", "5 分钟买入笔数占买卖总笔数比例；高于 50% 表示买方更活跃。"),
        "liquidity_stability_30m": factor_json(liquidity_stability_30m, "liquidity_stability_30m", "当前流动性除以过去 30 分钟流动性中位数；小于 1 表示流动性下降。")
    });
    let (raw_status, raw_label) = structure_status(&factors);
    let (status, label) = if stage_name == "inactive" || stage_name == "unknown" {
        ("neutral", "结构观察")
    } else {
        (raw_status, raw_label)
    };
    json!({
        "token_address": token,
        "pair_address": pair,
        "available": true,
        "reason": null,
        "updated_at": latest.at,
        "stage": stage_name,
        "stage_label": stage_label(&stage_name),
        "data_status": data_status,
        "data_status_label": data_status_label(&data_status),
        "risk_status": risk_status,
        "risk_status_label": risk_status_label(&risk_status),
        "factors": factors,
        "structure_status": status,
        "structure_label": label,
        "conclusion": conclusion(&stage_name, status, &factors),
        "timeline": stage_timeline(&stage_name),
        "b_at": stage["b_at"].clone(),
        "b_price": stage["b_price"].clone(),
        "b_breakout_line": stage["b_breakout_line"].clone(),
        "c_at": stage["c_at"].clone(),
        "c_breakout_line": stage["c_breakout_line"].clone(),
        "base_upper": stage["base_upper"].clone(),
        "base_lower": stage["base_lower"].clone()
    })
}

fn unavailable(
    token: &str,
    stage: String,
    data_status: String,
    risk_status: String,
    reason: &str,
) -> Value {
    json!({
        "token_address": token,
        "pair_address": null,
        "available": false,
        "reason": reason,
        "updated_at": null,
        "stage": stage,
        "stage_label": stage_label(&stage),
        "data_status": data_status,
        "data_status_label": data_status_label(&data_status),
        "risk_status": risk_status,
        "risk_status_label": risk_status_label(&risk_status),
        "factors": null,
        "structure_status": "neutral",
        "structure_label": "结构观察",
        "conclusion": reason,
        "timeline": stage_timeline(&stage)
    })
}

pub fn structures(path: &str, tokens: &[String], limit: usize) -> Result<Vec<Value>> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    Ok(tokens
        .iter()
        .take(limit)
        .map(|token| structure_for_token(&conn, token))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn factor_status_uses_conservative_thresholds() {
        assert_eq!(factor_status(0.03, "momentum_5m"), FactorStatus::Strong);
        assert_eq!(factor_status(-0.03, "breakout_30m"), FactorStatus::Weak);
        assert_eq!(factor_status(0.56, "buy_pressure_5m"), FactorStatus::Strong);
        assert_eq!(
            factor_status(1.6, "relative_volume_30m"),
            FactorStatus::Strong
        );
    }

    #[test]
    fn structure_status_requires_multiple_confirmation() {
        let factors = json!({
            "momentum_5m": {"status":"strong"},
            "momentum_15m": {"status":"strong"},
            "breakout_30m": {"status":"strong"},
            "relative_volume_30m": {"status":"strong"},
            "buy_pressure_5m": {"status":"neutral"},
            "liquidity_stability_30m": {"status":"neutral"}
        });
        assert_eq!(structure_status(&factors).1, "结构良好");
    }
}
