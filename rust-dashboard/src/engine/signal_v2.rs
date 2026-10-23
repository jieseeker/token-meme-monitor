//! Deterministic, fixed-pool shadow structure observations. Times are Unix seconds.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Sample {
    pub at: i64,
    pub pair_address: String,
    pub price: Option<f64>,
    pub volume_h1: Option<f64>,
    pub volume_m5: Option<f64>,
    pub liquidity_usd: Option<f64>,
    pub fingerprint: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Settings {
    pub min_volume_h1_usd: f64,
    pub focus_score_threshold: i32,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            min_volume_h1_usd: 15_000.0,
            focus_score_threshold: 65,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct State {
    pub token_address: String,
    pub pair_address: String,
    pub first_seen_at: i64,
    pub updated_at: i64,
    pub last_evidence_at: Option<i64>,
    pub stage: String,
    pub data_status: String,
    pub risk_status: String,
    pub terminated: bool,
    pub expired: bool,
    pub c_closed: bool,
    pub c_reason: Option<String>,
    pub b_at: Option<i64>,
    pub b_price: Option<f64>,
    pub reference_upper: Option<f64>,
    pub reference_samples: Vec<Sample>,
    pub b_crossed_at: Option<i64>,
    pub first_wave_high: Option<f64>,
    pub base_upper: Option<f64>,
    pub base_lower: Option<f64>,
    pub base_started_at: Option<i64>,
    pub base_frozen_at: Option<i64>,
    pub base_volume_m5: Option<f64>,
    pub base_volume_samples: Vec<(i64, f64)>,
    pub c_at: Option<i64>,
    pub last_price: Option<f64>,
    score_entry: Option<bool>,
    volume_entry: Option<bool>,
    last_sample: Option<Sample>,
    b_pending: Option<Confirmation>,
    b_rearmed: bool,
    support_failures: u8,
    base_window: Option<BaseWindow>,
    c_pending: Option<Confirmation>,
    c_rearmed: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Confirmation {
    at: i64,
    count: u8,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct BaseWindow {
    at: i64,
    first_price: f64,
    high: f64,
    low: f64,
    count: u32,
    // Only the first three sufficiently separated observations define the rolling m5 baseline.
    volumes: Vec<(i64, f64)>,
}

impl State {
    pub fn new(token: &str, sample: &Sample) -> Self {
        Self {
            token_address: token.into(),
            pair_address: sample.pair_address.clone(),
            first_seen_at: sample.at,
            updated_at: sample.at,
            last_evidence_at: None,
            stage: "inactive".into(),
            data_status: "warming_up".into(),
            risk_status: "HOLD".into(),
            terminated: false,
            expired: false,
            c_closed: false,
            c_reason: None,
            b_at: None,
            b_price: None,
            reference_upper: None,
            reference_samples: Vec::new(),
            b_crossed_at: None,
            first_wave_high: None,
            base_upper: None,
            base_lower: None,
            base_started_at: None,
            base_frozen_at: None,
            base_volume_m5: None,
            base_volume_samples: Vec::new(),
            c_at: None,
            last_price: None,
            score_entry: None,
            volume_entry: None,
            last_sample: None,
            b_pending: None,
            b_rearmed: true,
            support_failures: 0,
            base_window: None,
            c_pending: None,
            c_rearmed: true,
        }
    }
}

fn nonnegative(value: Option<f64>) -> Option<f64> {
    value.filter(|v| v.is_finite() && *v >= 0.0)
}
fn price(sample: &Sample) -> Option<f64> {
    nonnegative(sample.price).filter(|v| *v > 0.0)
}
fn valid(sample: &Sample) -> bool {
    !sample.fingerprint.is_empty()
        && !sample.pair_address.is_empty()
        && price(sample).is_some()
        && [sample.volume_h1, sample.volume_m5, sample.liquidity_usd]
            .iter()
            .all(|v| v.is_none() || nonnegative(*v).is_some())
}
fn median(mut values: Vec<f64>) -> f64 {
    values.sort_by(f64::total_cmp);
    let mid = values.len() / 2;
    if values.len() % 2 == 0 {
        values[mid - 1] / 2.0 + values[mid] / 2.0
    } else {
        values[mid]
    }
}

fn content(sample: &Sample) -> [Option<u64>; 4] {
    [
        sample.price,
        sample.volume_h1,
        sample.volume_m5,
        sample.liquidity_usd,
    ]
    .map(|value| value.map(|v| if v == 0.0 { 0 } else { v.to_bits() }))
}
fn comparable_history<'a>(
    sample: &Sample,
    history: &'a [Sample],
    min_at: i64,
    max_at: i64,
) -> Vec<&'a Sample> {
    let mut selected: Vec<_> = history
        .iter()
        .filter(|s| {
            s.pair_address == sample.pair_address && valid(s) && s.at >= min_at && s.at <= max_at
        })
        .collect();
    selected.sort_by(|a, b| a.at.cmp(&b.at).then(a.fingerprint.cmp(&b.fingerprint)));
    let mut fingerprints = std::collections::BTreeSet::new();
    let mut contents = std::collections::BTreeSet::new();
    selected.retain(|s| fingerprints.insert(s.fingerprint.as_str()) && contents.insert(content(s)));
    selected
}

fn background(sample: &Sample, history: &[Sample]) -> Option<f64> {
    let mut buckets = std::collections::BTreeMap::new();
    for s in comparable_history(
        sample,
        history,
        sample.at.saturating_sub(86_400),
        sample.at.saturating_sub(3600),
    ) {
        if s.volume_h1.is_some() {
            let entry = buckets.entry(s.at.div_euclid(3600)).or_insert(s);
            if s.at > entry.at {
                *entry = s;
            }
        }
    }
    let selected: Vec<_> = buckets.values().copied().collect();
    if selected.len() < 12
        || selected.first()?.at > sample.at.saturating_sub(72_000)
        || selected.last()?.at < sample.at.saturating_sub(10_800)
        || selected.windows(2).any(|w| w[1].at - w[0].at > 7200)
    {
        return None;
    }
    Some(median(
        selected.iter().map(|s| s.volume_h1.unwrap()).collect(),
    ))
}
fn price_reference(sample: &Sample, history: &[Sample]) -> Option<Vec<Sample>> {
    let mut buckets = std::collections::BTreeMap::new();
    let mut earliest = sample.at;
    let mut latest = i64::MIN;
    for s in comparable_history(
        sample,
        history,
        sample.at.saturating_sub(3600),
        sample.at.saturating_sub(1),
    ) {
        let entry = buckets.entry(s.at.div_euclid(300)).or_insert(s);
        if price(s) > price(entry) {
            *entry = s;
        }
        earliest = earliest.min(s.at);
        latest = latest.max(s.at);
    }
    (buckets.len() >= 4 && latest.saturating_sub(earliest) >= 2700)
        .then(|| buckets.values().map(|s| (*s).clone()).collect())
}
fn volume_hit(current: Option<f64>, baseline: Option<f64>, ratio: f64, minimum: f64) -> bool {
    matches!((nonnegative(current),baseline), (Some(v),Some(b)) if v >= minimum && v >= b * ratio)
}
fn risk_status(sample: &Sample, risks: &[String]) -> String {
    if sample.liquidity_usd == Some(0.0)
        || risks.iter().any(|r| {
            matches!(
                r.as_str(),
                "BLOCK" | "liquidity_near_zero" | "fdv_liquidity_stretched"
            )
        })
    {
        "BLOCK"
    } else if sample.liquidity_usd.is_none()
        || risks.iter().any(|r| {
            matches!(
                r.as_str(),
                "HOLD" | "liquidity_withdrawal" | "missing_price" | "stale_pair"
            )
        })
    {
        "HOLD"
    } else if !risks.is_empty() {
        "WARN"
    } else {
        "PASS"
    }
    .into()
}
fn close_c(state: &mut State, reason: &str) {
    if state.c_closed {
        return;
    }
    state.c_closed = true;
    state.c_reason = Some(reason.into());
    state.base_window = None;
    state.c_pending = None;
}

/// Advance only from a new, valid observation of the event's frozen reference pool.
pub fn advance(
    state: &mut State,
    sample: &Sample,
    history: &[Sample],
    score: i32,
    risks: &[String],
    settings: &Settings,
) {
    if sample.at < state.updated_at {
        return;
    }
    tick(state, sample.at);
    if state.expired {
        return;
    }
    if sample.pair_address != state.pair_address {
        state.data_status = "source_changed".into();
        state.b_pending = None;
        state.b_rearmed = false;
        if state.b_at.is_some() {
            close_c(state, "source_changed");
        }
        return;
    }
    state.risk_status = risk_status(sample, risks);
    if !valid(sample) {
        state.data_status = "invalid_input".into();
        return;
    }
    if let Some(last) = &state.last_sample {
        if sample.at <= last.at
            || sample.fingerprint == last.fingerprint
            || (sample.price == last.price
                && sample.volume_h1 == last.volume_h1
                && sample.volume_m5 == last.volume_m5
                && sample.liquidity_usd == last.liquidity_usd)
        {
            return;
        }
    }
    let p = price(sample).unwrap();
    let previous_price = state.last_price;
    state.last_evidence_at = Some(sample.at);
    state.last_sample = Some(sample.clone());
    state.last_price = Some(p);
    let baseline = background(sample, history);
    state.data_status = if sample.volume_h1.is_none() {
        "missing_h1"
    } else if sample.liquidity_usd.is_none() {
        "missing_liquidity"
    } else if state.b_at.is_some() && sample.volume_m5.is_none() {
        "missing_m5"
    } else if baseline.is_none() && state.b_at.is_none() {
        "warming_up"
    } else {
        "current"
    }
    .into();
    let score_hit = score >= settings.focus_score_threshold;
    let volume =
        baseline.map(|_| volume_hit(sample.volume_h1, baseline, 1.5, settings.min_volume_h1_usd));
    let entry = (score_hit && state.score_entry != Some(true))
        || (volume == Some(true) && state.volume_entry != Some(true));
    state.score_entry = Some(score_hit);
    if let Some(volume) = volume {
        state.volume_entry = Some(volume);
    }
    if (state.terminated || state.stage == "inactive") && entry {
        let score_entry = state.score_entry;
        let volume_entry = state.volume_entry;
        *state = State::new(&state.token_address.clone(), sample);
        state.stage = "watch".into();
        state.score_entry = score_entry;
        state.volume_entry = volume_entry;
        state.last_evidence_at = Some(sample.at);
        state.last_sample = Some(sample.clone());
        state.last_price = Some(p);
        state.risk_status = risk_status(sample, risks);
        state.data_status = if baseline.is_some() {
            "current"
        } else {
            "warming_up"
        }
        .into();
        return;
    }
    if state.terminated || state.stage == "inactive" {
        return;
    }
    if state.b_at.is_none() {
        advance_b(state, sample, history, baseline, previous_price, settings);
        return;
    }
    let support = state.reference_upper.unwrap() * 0.995;
    state.support_failures = if p < support {
        state.support_failures + 1
    } else {
        0
    };
    if state.support_failures >= 2 {
        state.terminated = true;
        close_c(state, "support_failed");
        return;
    }
    if !state.c_closed {
        advance_c(state, sample, settings);
    }
}

fn advance_b(
    state: &mut State,
    sample: &Sample,
    history: &[Sample],
    baseline: Option<f64>,
    previous_price: Option<f64>,
    settings: &Settings,
) {
    let p = price(sample).unwrap();
    let volume = volume_hit(sample.volume_h1, baseline, 2.0, settings.min_volume_h1_usd);
    if let Some(upper) = state.reference_upper {
        if p <= upper * 1.005 && state.b_pending.is_none() {
            state.b_rearmed = true;
        }
    }
    if let Some(mut pending) = state.b_pending.take() {
        let upper = state.reference_upper.unwrap();
        if p < upper || !volume || sample.at - pending.at > 900 {
            state.b_rearmed = p <= upper * 1.005;
            return;
        }
        pending.count = pending.count.saturating_add(1);
        if pending.count >= 3 && sample.at - pending.at >= 240 && p > upper * 1.005 {
            state.b_at = Some(sample.at);
            state.b_price = Some(p);
            state.first_wave_high = Some(p);
            state.stage = if p > upper * 1.005 * 1.08 {
                "first_start_extended"
            } else {
                "first_start"
            }
            .into();
        } else {
            state.b_pending = Some(pending);
        }
        return;
    }
    if !state.b_rearmed {
        return;
    }
    if let Some(reference) = price_reference(sample, history) {
        let upper = reference.iter().filter_map(price).fold(0.0, f64::max);
        let line = upper * 1.005;
        if p > line && previous_price.is_some_and(|previous| previous <= line) {
            state.reference_upper = Some(upper);
            state.reference_samples = reference;
            state.b_crossed_at = Some(sample.at);
            state.b_rearmed = false;
            if volume {
                state.b_pending = Some(Confirmation {
                    at: sample.at,
                    count: 1,
                });
            }
        }
    }
}

fn advance_c(state: &mut State, sample: &Sample, settings: &Settings) {
    let p = price(sample).unwrap();
    if let Some(upper) = state.base_upper {
        let lower = state.base_lower.unwrap();
        if p < lower * 0.995 {
            close_c(state, "base_lower_failed");
            return;
        }
        let line = upper * 1.005;
        let volume = volume_hit(
            sample.volume_m5,
            state.base_volume_m5,
            1.5,
            settings.min_volume_h1_usd * 0.12,
        );
        if p <= line {
            state.c_pending = None;
            state.c_rearmed = true;
            return;
        }
        if let Some(pending) = state.c_pending.take() {
            if !volume {
                state.c_rearmed = false;
                return;
            }
            if sample.at - pending.at < 60 {
                state.c_pending = Some(pending);
                return;
            }
            if sample.at - pending.at <= 300 {
                state.c_at = Some(sample.at);
                state.stage = if p > line * 1.08 {
                    "second_start_extended"
                } else {
                    "second_start"
                }
                .into();
                close_c(state, "confirmed");
            } else {
                state.c_rearmed = false;
            }
        } else if state.c_rearmed {
            state.c_rearmed = false;
            if volume {
                state.c_pending = Some(Confirmation {
                    at: sample.at,
                    count: 1,
                });
            }
        }
        return;
    }
    let b_price = state.b_price.unwrap();
    if state.base_window.is_none() {
        let high = state.first_wave_high.unwrap().max(p);
        state.first_wave_high = Some(high);
        if high < b_price * 1.08 {
            return;
        }
        state.stage = "first_wave".into();
        let retrace = (high - p) / (high - b_price);
        if retrace > 0.60 {
            state.terminated = true;
            close_c(state, "retrace_exceeded");
            return;
        }
        if p > high * 0.98 || retrace < 0.20 {
            return;
        }
        state.stage = "pullback".into();
    } else {
        let high = state.first_wave_high.unwrap();
        if p > high {
            close_c(state, "first_wave_continuation");
            return;
        }
        if (high - p) / (high - b_price) > 0.60 {
            state.terminated = true;
            close_c(state, "retrace_exceeded");
            return;
        }
    }
    let restart = state.base_window.as_ref().is_none_or(|window| {
        p < window.first_price * 0.995 || window.high.max(p) / window.low.min(p) > 1.03
    });
    if restart {
        state.base_window = Some(BaseWindow {
            at: sample.at,
            first_price: p,
            high: p,
            low: p,
            count: 0,
            volumes: Vec::with_capacity(3),
        });
    }
    let window = state.base_window.as_mut().unwrap();
    window.high = window.high.max(p);
    window.low = window.low.min(p);
    window.count = window.count.saturating_add(1);
    if let Some(v) = nonnegative(sample.volume_m5) {
        if window.volumes.len() < 3
            && window
                .volumes
                .last()
                .is_none_or(|(at, _)| sample.at - at >= 300)
        {
            window.volumes.push((sample.at, v));
        }
    }
    if sample.at - window.at >= 600 && window.count >= 3 && window.volumes.len() == 3 {
        state.base_upper = Some(window.high);
        state.base_lower = Some(window.low);
        state.base_started_at = Some(window.at);
        state.base_frozen_at = Some(sample.at);
        state.base_volume_m5 = Some(median(window.volumes.iter().map(|(_, v)| *v).collect()));
        state.base_volume_samples = window.volumes.clone();
        state.base_window = None;
        state.stage = "base".into();
    }
}

/// Apply retention and evidence continuity even when the provider produces no usable sample.
pub fn tick(state: &mut State, now: i64) {
    if now < state.updated_at {
        return;
    }
    state.updated_at = now;
    if now.saturating_sub(state.first_seen_at) >= 7 * 86_400 {
        state.expired = true;
        state.terminated = true;
        state.data_status = "expired".into();
        close_c(state, "event_expired");
        return;
    }
    if state
        .b_at
        .is_some_and(|at| now.saturating_sub(at) >= 86_400)
    {
        close_c(state, "window_expired");
    }
    if state
        .last_evidence_at
        .is_some_and(|at| now.saturating_sub(at) > 300)
    {
        state.data_status = "interrupted".into();
        if state.b_pending.take().is_some() {
            state.b_rearmed = false;
        }
        if state.b_at.is_some() {
            close_c(state, "interrupted");
        }
    }
}

pub fn summary(state: &State) -> Value {
    json!({
        "strategy_version": "signal_v2_shadow_v1", "token_address": state.token_address,
        "pair_address": state.pair_address, "first_seen_at": state.first_seen_at,
        "updated_at": state.updated_at, "last_evidence_at": state.last_evidence_at,
        "stage": state.stage, "data_status": state.data_status, "risk_status": state.risk_status,
        "source_time_unverified": true, "opportunity_eligible": false,
        "terminated": state.terminated, "expired": state.expired, "c_closed": state.c_closed,
        "c_reason": state.c_reason, "b_at": state.b_at, "b_price": state.b_price,
        "reference_upper": state.reference_upper, "reference_samples": state.reference_samples,
        "b_crossed_at": state.b_crossed_at,
        "b_breakout_line": state.reference_upper.map(|p| p * 1.005),
        "first_wave_high": state.first_wave_high,
        "base_upper": state.base_upper, "base_lower": state.base_lower,
        "base_started_at": state.base_started_at, "base_frozen_at": state.base_frozen_at,
        "base_volume_m5": state.base_volume_m5, "base_volume_samples": state.base_volume_samples,
        "c_breakout_line": state.base_upper.map(|p| p * 1.005), "c_at": state.c_at,
        "distance_from_first_wave_high_pct": state.last_price.zip(state.first_wave_high)
            .map(|(p,h)| (p/h-1.0)*100.0),
        "oi_required": false, "depth_check": "unchecked", "coverage": "reference_pool"
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    const T: i64 = 100 * 86_400;
    fn sample(at: i64, price: f64) -> Sample {
        Sample {
            at,
            pair_address: "pool".into(),
            price: Some(price),
            volume_h1: Some(30_000.0),
            volume_m5: Some(3_000.0),
            liquidity_usd: Some(50_000.0),
            fingerprint: format!("request-{at}-{price}"),
        }
    }
    fn history() -> Vec<Sample> {
        let mut samples: Vec<_> = (1..=24)
            .map(|h| {
                let mut s = sample(T - h * 3600, 100.0);
                s.volume_h1 = Some(10_000.0);
                s.liquidity_usd = Some(50_000.0 + h as f64);
                s
            })
            .collect();
        samples.extend((-4..=8).map(|q| {
            let mut s = sample(T + q * 900, 100.0);
            s.liquidity_usd = Some(60_000.0 + q as f64);
            s
        }));
        samples
    }
    fn feed(state: &mut State, at: i64, price: f64) {
        advance(
            state,
            &sample(at, price),
            &history(),
            70,
            &[],
            &Settings::default(),
        );
    }
    fn watch() -> State {
        let mut state = State::new("token", &sample(T, 100.0));
        feed(&mut state, T, 100.0);
        state
    }
    fn first_start() -> State {
        let mut state = watch();
        for (at, p) in [(T + 120, 101.0), (T + 240, 101.1), (T + 360, 101.2)] {
            feed(&mut state, at, p);
        }
        assert_eq!(state.stage, "first_start");
        state
    }
    fn base() -> State {
        let mut state = first_start();
        // B=101.2, peak=112; retrace to 108 returns 37% of the observed first wave.
        for (at, p) in [
            (T + 600, 112.0),
            (T + 840, 108.0),
            (T + 1080, 108.1),
            (T + 1320, 108.2),
            (T + 1560, 108.1),
            (T + 1800, 108.2),
        ] {
            feed(&mut state, at, p);
        }
        assert_eq!(state.stage, "base");
        state
    }
    #[test]
    fn first_observation_can_only_enter_watch_even_with_high_score() {
        let mut s = State::new("token", &sample(T, 120.0));
        feed(&mut s, T, 120.0);
        assert_eq!(s.stage, "watch");
        assert!(summary(&s)["b_at"].is_null());
        assert_eq!(summary(&s)["source_time_unverified"], true);
        assert_eq!(summary(&s)["opportunity_eligible"], false);
    }
    #[test]
    fn first_start_freezes_reference_and_requires_two_later_observations() {
        let mut s = watch();
        feed(&mut s, T + 120, 101.0);
        feed(&mut s, T + 240, 101.1);
        assert_eq!(s.stage, "watch");
        let mut h = history();
        h.push(sample(T + 300, 200.0)); // unseen future must not raise the frozen line
        advance(
            &mut s,
            &sample(T + 360, 101.2),
            &h,
            70,
            &[],
            &Settings::default(),
        );
        assert_eq!(s.stage, "first_start");
        assert_eq!(summary(&s)["reference_upper"], 100.0);
        assert_eq!(summary(&s)["b_at"], T + 360);
    }
    #[test]
    fn baseline_requires_hourly_coverage_and_matching_valid_source() {
        for kind in 0..5 {
            let mut h = history();
            match kind {
                0 => h.retain(|x| x.at > T - 12 * 3600),
                1 => h.retain(|x| x.at > T - 19 * 3600),
                2 => h.retain(|x| x.at < T - 4 * 3600),
                3 => h.retain(|x| x.at < T - 10 * 3600 || x.at > T - 6 * 3600),
                _ => h.iter_mut().for_each(|x| x.pair_address = "other".into()),
            }
            let mut s = State::new("token", &sample(T, 100.0));
            for (at, p) in [
                (T, 100.0),
                (T + 120, 101.0),
                (T + 240, 101.1),
                (T + 360, 101.2),
            ] {
                advance(&mut s, &sample(at, p), &h, 0, &[], &Settings::default());
            }
            assert_eq!(s.stage, "inactive", "invalid baseline {kind}");
        }
    }
    #[test]
    fn missing_is_not_zero_and_nonfinite_inputs_cannot_confirm() {
        let mut zeros = history();
        zeros.iter_mut().for_each(|s| s.volume_h1 = Some(0.0));
        let mut s = State::new("token", &sample(T, 100.0));
        advance(
            &mut s,
            &sample(T, 100.0),
            &zeros,
            0,
            &[],
            &Settings::default(),
        );
        assert_eq!(s.stage, "watch");
        let mut missing = history();
        missing.iter_mut().for_each(|s| s.volume_h1 = None);
        let mut s = State::new("token", &sample(T, 100.0));
        advance(
            &mut s,
            &sample(T, 100.0),
            &missing,
            0,
            &[],
            &Settings::default(),
        );
        assert_eq!(s.stage, "inactive");
        for bad in [f64::NAN, f64::INFINITY, -1.0, 0.0] {
            let mut s = watch();
            let mut input = sample(T + 120, bad);
            advance(&mut s, &input, &history(), 99, &[], &Settings::default());
            assert_eq!(s.last_evidence_at, Some(T));
            assert_eq!(s.stage, "watch");
            input.price = Some(101.0);
            input.volume_h1 = Some(-1.0);
            advance(&mut s, &input, &history(), 99, &[], &Settings::default());
            assert_eq!(s.last_evidence_at, Some(T));
        }
    }
    #[test]
    fn duplicate_content_fingerprint_and_out_of_order_do_not_renew_evidence() {
        let mut s = watch();
        let original = sample(T, 100.0);
        let mut duplicate = original.clone();
        duplicate.at = T + 100;
        advance(
            &mut s,
            &duplicate,
            &history(),
            99,
            &[],
            &Settings::default(),
        );
        assert_eq!(s.last_evidence_at, Some(T));
        let mut same_content = sample(T + 200, 100.0);
        same_content.fingerprint = "new-request".into();
        advance(
            &mut s,
            &same_content,
            &history(),
            99,
            &[],
            &Settings::default(),
        );
        assert_eq!(s.last_evidence_at, Some(T));
        feed(&mut s, T - 1, 105.0);
        assert_eq!(s.last_evidence_at, Some(T));
    }
    #[test]
    fn wrong_pool_ends_c_without_changing_reference_pool_or_stage() {
        let mut s = first_start();
        let mut input = sample(T + 480, 110.0);
        input.pair_address = "other".into();
        advance(&mut s, &input, &history(), 99, &[], &Settings::default());
        assert_eq!(s.stage, "first_start");
        assert_eq!(s.pair_address, "pool");
        assert_eq!(summary(&s)["c_closed"], true);
        assert_eq!(s.data_status, "source_changed");
    }
    #[test]
    fn complete_second_start_uses_prospective_base_and_is_once_per_event() {
        let mut s = base();
        assert_eq!(summary(&s)["base_upper"], 108.2);
        feed(&mut s, T + 1920, 109.0);
        assert_eq!(s.stage, "base");
        let mut input = sample(T + 1980, 109.1);
        input.volume_m5 = Some(5_000.0);
        // The first cross must also have volume; reset below the frozen line before another try.
        advance(&mut s, &input, &history(), 70, &[], &Settings::default());
        assert_eq!(s.stage, "base");
        feed(&mut s, T + 2040, 108.3);
        for (at, p) in [(T + 2100, 109.0), (T + 2160, 109.1)] {
            let mut input = sample(at, p);
            input.volume_m5 = Some(5_000.0);
            advance(&mut s, &input, &history(), 70, &[], &Settings::default());
        }
        assert_eq!(s.stage, "second_start");
        assert_eq!(summary(&s)["c_at"], T + 2160);
        assert_eq!(summary(&s)["base_upper"], 108.2);
        feed(&mut s, T + 2280, 120.0);
        assert_eq!(summary(&s)["c_at"], T + 2160);
        assert!(
            summary(&s)["distance_from_first_wave_high_pct"]
                .as_f64()
                .unwrap()
                > 0.0
        );
    }
    #[test]
    fn base_waits_for_three_five_minute_volume_samples() {
        let mut s = first_start();
        for (at, p) in [
            (T + 600, 112.0),
            (T + 840, 108.0),
            (T + 1080, 108.1),
            (T + 1320, 108.2),
            (T + 1560, 108.1),
        ] {
            feed(&mut s, at, p);
        }
        assert_eq!(s.stage, "pullback");
        assert!(summary(&s)["base_upper"].is_null());
        feed(&mut s, T + 1800, 108.2);
        assert_eq!(s.stage, "base");
    }
    #[test]
    fn lower_low_restarts_window_without_backfilling_base() {
        let mut s = first_start();
        for (at, p) in [
            (T + 600, 112.0),
            (T + 840, 108.0),
            (T + 1080, 108.1),
            (T + 1320, 107.4),
            (T + 1560, 107.5),
            (T + 1800, 107.6),
        ] {
            feed(&mut s, at, p);
        }
        assert!(summary(&s)["base_upper"].is_null());
        feed(&mut s, T + 2040, 107.5);
        feed(&mut s, T + 2280, 107.6);
        assert_eq!(summary(&s)["base_upper"], 107.6);
        assert_eq!(summary(&s)["base_started_at"], T + 1320);
    }
    #[test]
    fn missing_m5_and_h1_wave_volume_never_substitute_for_c_volume() {
        let mut s = first_start();
        for (at, p) in [
            (T + 600, 112.0),
            (T + 840, 108.0),
            (T + 1080, 108.1),
            (T + 1320, 108.2),
            (T + 1560, 108.1),
            (T + 1800, 108.2),
        ] {
            let mut input = sample(at, p);
            input.volume_m5 = None;
            advance(&mut s, &input, &history(), 70, &[], &Settings::default());
        }
        assert!(summary(&s)["base_upper"].is_null());
        feed(&mut s, T + 1920, 113.0);
        assert_eq!(summary(&s)["c_closed"], true);
        let mut s = base();
        for (at, p) in [(T + 1920, 109.0), (T + 2040, 109.1)] {
            let mut input = sample(at, p);
            input.volume_h1 = Some(1_000_000.0);
            input.volume_m5 = None;
            advance(&mut s, &input, &history(), 99, &[], &Settings::default());
        }
        assert!(summary(&s)["c_at"].is_null());
    }
    #[test]
    fn pre_b_rise_and_shallow_wave_cannot_create_second_start() {
        let mut s = first_start();
        for (at, p) in [
            (T + 600, 106.0),
            (T + 840, 103.0),
            (T + 1080, 103.1),
            (T + 1320, 103.2),
            (T + 1560, 104.0),
        ] {
            feed(&mut s, at, p);
        }
        assert_eq!(s.stage, "first_start");
        assert!(summary(&s)["base_upper"].is_null());
    }
    #[test]
    fn gap_and_restart_preserve_last_stage_but_never_resume_c() {
        let mut s = first_start();
        tick(&mut s, T + 661);
        assert_eq!(s.stage, "first_start");
        assert_eq!(s.data_status, "interrupted");
        let encoded = serde_json::to_string(&s).unwrap();
        let mut restored: State = serde_json::from_str(&encoded).unwrap();
        feed(&mut restored, T + 700, 112.0);
        assert_eq!(summary(&restored)["c_closed"], true);
        assert!(summary(&restored)["base_upper"].is_null());
        let mut s = watch();
        feed(&mut s, T + 120, 101.0);
        tick(&mut s, T + 421);
        feed(&mut s, T + 480, 101.1);
        feed(&mut s, T + 600, 101.2);
        feed(&mut s, T + 720, 101.3);
        assert_eq!(s.stage, "watch");
        feed(&mut s, T + 840, 100.4);
        feed(&mut s, T + 960, 101.0);
        feed(&mut s, T + 1080, 101.1);
        feed(&mut s, T + 1200, 101.2);
        assert_eq!(s.stage, "first_start");
    }
    #[test]
    fn support_failure_requires_two_observations_and_entries_must_reset() {
        let mut s = first_start();
        feed(&mut s, T + 480, 99.4);
        assert!(!summary(&s)["terminated"].as_bool().unwrap());
        feed(&mut s, T + 600, 99.3);
        assert_eq!(summary(&s)["terminated"], true);
        feed(&mut s, T + 720, 101.0);
        assert_eq!(summary(&s)["terminated"], true);
        let mut low = sample(T + 840, 100.0);
        low.volume_h1 = Some(10_000.0);
        advance(&mut s, &low, &history(), 0, &[], &Settings::default());
        feed(&mut s, T + 960, 100.1);
        assert_eq!(s.stage, "watch");
        assert_eq!(s.first_seen_at, T + 960);
    }
    #[test]
    fn risk_precedence_keeps_structure_facts_separate() {
        let mut s = watch();
        for (flags, want) in [
            (vec!["sell_pressure"], "WARN"),
            (vec!["liquidity_withdrawal", "sell_pressure"], "HOLD"),
            (
                vec!["fdv_liquidity_stretched", "liquidity_withdrawal"],
                "BLOCK",
            ),
        ] {
            let mut input = sample(s.updated_at + 60, 100.1);
            input.volume_m5 = Some(input.at as f64);
            advance(
                &mut s,
                &input,
                &history(),
                99,
                &flags.iter().map(|x| x.to_string()).collect::<Vec<_>>(),
                &Settings::default(),
            );
            assert_eq!(s.risk_status, want);
            assert_eq!(summary(&s)["opportunity_eligible"], false);
        }
        assert_eq!(s.stage, "watch");
    }
    #[test]
    fn expiry_is_anchored_to_event_start_and_c_window_to_b_time() {
        let mut s = first_start();
        tick(&mut s, T + 360 + 86_400);
        assert_eq!(summary(&s)["c_closed"], true);
        assert_eq!(s.stage, "first_start");
        feed(&mut s, T + 7 * 86_400, 103.0);
        assert_eq!(summary(&s)["expired"], true);
        assert_eq!(s.first_seen_at, T);
    }
    #[test]
    fn extended_confirmation_is_recorded_instead_of_fresh_start() {
        let mut s = watch();
        feed(&mut s, T + 120, 101.0);
        feed(&mut s, T + 240, 102.0);
        feed(&mut s, T + 360, 110.0);
        assert_eq!(s.stage, "first_start_extended");
        let mut s = base();
        for (at, p) in [(T + 1920, 109.0), (T + 2040, 119.0)] {
            let mut input = sample(at, p);
            input.volume_m5 = Some(5_000.0);
            advance(&mut s, &input, &history(), 70, &[], &Settings::default());
        }
        assert_eq!(s.stage, "second_start_extended");
    }
    #[test]
    fn frozen_base_lower_break_ends_c_and_deep_retrace_invalidates_structure() {
        let mut s = base();
        feed(&mut s, T + 1920, 107.4);
        assert_eq!(summary(&s)["c_closed"], true);
        let mut s = first_start();
        feed(&mut s, T + 600, 112.0);
        feed(&mut s, T + 840, 105.0);
        assert_eq!(summary(&s)["terminated"], true);
    }
    #[test]
    fn frozen_evidence_keeps_bounded_reference_samples_and_volume_observations() {
        let s = first_start();
        let result = summary(&s);
        assert_eq!(
            result["reference_samples"].as_array().map(Vec::len),
            Some(4)
        );
        assert!(result["reference_samples"]
            .as_array()
            .unwrap()
            .iter()
            .all(|s| s["at"].as_i64().unwrap() < T + 120));
        let s = base();
        let result = summary(&s);
        assert_eq!(
            result["base_volume_samples"],
            json!([[T + 840, 3000.0], [T + 1320, 3000.0], [T + 1800, 3000.0]])
        );
    }

    #[test]
    fn cached_history_cannot_manufacture_hourly_or_price_coverage() {
        for by_fingerprint in [true, false] {
            let mut h = history();
            for old in &mut h {
                if by_fingerprint {
                    old.fingerprint = "one-observation".into();
                } else {
                    old.price = Some(100.0);
                    old.volume_h1 = Some(10_000.0);
                    old.volume_m5 = Some(100.0);
                    old.liquidity_usd = Some(50_000.0);
                }
            }
            let mut s = State::new("token", &sample(T, 100.0));
            advance(&mut s, &sample(T, 100.0), &h, 0, &[], &Settings::default());
            assert_eq!(
                s.stage, "inactive",
                "cached baseline by fingerprint={by_fingerprint}"
            );
        }
    }
    #[test]
    fn baseline_never_uses_future_recent_or_legacy_missing_fields() {
        let mut h = history();
        h.retain(|s| s.at > T - 3600);
        for old in &mut h {
            old.volume_h1 = Some(0.0);
        }
        let mut s = State::new("token", &sample(T, 100.0));
        advance(&mut s, &sample(T, 100.0), &h, 0, &[], &Settings::default());
        assert_eq!(s.stage, "inactive");
        let mut h = history();
        for old in &mut h {
            old.fingerprint.clear();
        }
        let mut s = State::new("token", &sample(T, 100.0));
        advance(&mut s, &sample(T, 100.0), &h, 0, &[], &Settings::default());
        assert_eq!(s.stage, "inactive");
    }
    #[test]
    fn b_requires_price_bucket_coverage_volume_and_minimum_hold_duration() {
        for kind in 0..3 {
            let mut s = watch();
            let mut h = history();
            if kind == 0 {
                h.retain(|x| x.at < T - 3600 || x.at > T - 1800);
            }
            for (at, p) in [(T + 60, 101.0), (T + 120, 101.1), (T + 180, 101.2)] {
                let mut input = sample(at, p);
                if kind == 1 {
                    input.volume_h1 = Some(19_999.0);
                }
                advance(&mut s, &input, &h, 99, &[], &Settings::default());
            }
            assert_eq!(s.stage, "watch");
            if kind == 2 {
                feed(&mut s, T + 300, 101.3);
                assert_eq!(s.stage, "first_start");
            }
        }
    }
    #[test]
    fn c_requires_sixty_seconds_and_no_more_than_five_minutes() {
        let mut s = base();
        for (at, p) in [(T + 1920, 109.0), (T + 1979, 109.1)] {
            let mut input = sample(at, p);
            input.volume_m5 = Some(5_000.0);
            advance(&mut s, &input, &history(), 99, &[], &Settings::default());
        }
        assert!(summary(&s)["c_at"].is_null());
        let mut input = sample(T + 1980, 109.2);
        input.volume_m5 = Some(5_000.0);
        advance(&mut s, &input, &history(), 99, &[], &Settings::default());
        assert_eq!(s.c_at, Some(T + 1980));
        let mut s = base();
        let mut input = sample(T + 1920, 109.0);
        input.volume_m5 = Some(5_000.0);
        advance(&mut s, &input, &history(), 99, &[], &Settings::default());
        input = sample(T + 2221, 109.1);
        input.volume_m5 = Some(5_000.0);
        advance(&mut s, &input, &history(), 99, &[], &Settings::default());
        assert!(s.c_at.is_none());
        assert!(s.c_closed);
    }
    #[test]
    fn c_window_expires_exactly_at_twenty_four_hours_even_with_continuous_data() {
        let mut s = first_start();
        let end = T + 360 + 86_400;
        for at in ((T + 660)..end).step_by(300) {
            let mut input = sample(at, 101.2);
            input.volume_m5 = Some(at as f64);
            advance(&mut s, &input, &history(), 70, &[], &Settings::default());
        }
        tick(&mut s, end - 1);
        assert!(!s.c_closed);
        tick(&mut s, end);
        assert!(s.c_closed);
        assert_eq!(s.c_reason.as_deref(), Some("window_expired"));
    }
}
