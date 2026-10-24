//! Small snapshot metadata and independent shadow persistence.
use super::features::PairSnapshot;
use super::signal_v2::{self, Sample, Settings, State};
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};

pub const EXPERIMENT_VERSION: &str = "signal_v2_shadow_v1";

pub fn settings_valid(settings: &Settings) -> bool {
    settings.min_volume_h1_usd.is_finite() && settings.min_volume_h1_usd > 0.0
}

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

pub fn metadata(pair: &Value, at: &str) -> Value {
    let price = pair["priceUsd"]
        .as_str()
        .and_then(|v| v.parse::<f64>().ok())
        .or_else(|| pair["priceUsd"].as_f64());
    json!({"signal_v2": {
        "version": 1, "provider": "dexscreener",
        "request_id": format!("dex-{}-{at}", std::process::id()),
        "request_completed_at": at, "source_time_unverified": true,
        "presence": {"price_usd": price.is_some(),
            "volume_h1": pair["volume"]["h1"].is_number(),
            "volume_m5": pair["volume"]["m5"].is_number(),
            "liquidity_usd": pair["liquidity"]["usd"].is_number()}
    }})
}

pub(crate) fn decode(
    pair: &str,
    at: &str,
    values: [Option<f64>; 4],
    raw: &Value,
) -> Option<Sample> {
    let meta = raw.get("signal_v2")?;
    if meta["version"] != 1
        || meta["provider"] != "dexscreener"
        || meta["source_time_unverified"] != true
        || meta["request_completed_at"].as_str()? != at
    {
        return None;
    }
    let id = meta["request_id"].as_str()?;
    if id.is_empty() || id.len() > 128 || pair.is_empty() {
        return None;
    }
    let mut decoded = [None; 4];
    for (index, key) in ["price_usd", "volume_h1", "volume_m5", "liquidity_usd"]
        .iter()
        .enumerate()
    {
        if meta["presence"][key].as_bool()? {
            let value = values[index]?;
            if !value.is_finite() || value < 0.0 {
                return None;
            }
            decoded[index] = Some(value);
        }
    }
    Some(Sample {
        at: chrono::DateTime::parse_from_rfc3339(at).ok()?.timestamp(),
        pair_address: pair.into(),
        price: decoded[0],
        volume_h1: decoded[1],
        volume_m5: decoded[2],
        liquidity_usd: decoded[3],
        fingerprint: id.into(),
    })
}

pub fn sample(snapshot: &PairSnapshot, raw: &Value) -> Option<Sample> {
    decode(
        &snapshot.pair_address,
        &snapshot.observed_at,
        [
            snapshot.price_usd,
            Some(snapshot.volume_h1),
            Some(snapshot.volume_m5),
            snapshot.liquidity_usd,
        ],
        raw,
    )
}

fn connect(path: &str) -> Result<Connection> {
    let conn = Connection::open(path)?;
    conn.busy_timeout(std::time::Duration::from_millis(100))?;
    Ok(conn)
}

pub fn history(conn: &Connection, token: &str, pair: &str, at: i64) -> Result<Vec<Sample>> {
    let from = chrono::DateTime::from_timestamp(at.saturating_sub(86400), 0)
        .ok_or("invalid history time")?
        .to_rfc3339();
    let until = chrono::DateTime::from_timestamp(at, 0)
        .ok_or("invalid history time")?
        .to_rfc3339();
    // shortcut: 4096 observations bound a 24h read; revisit if refresh becomes faster than 22 seconds.
    let mut query = conn.prepare("SELECT observed_at,price_usd,volume_h1,volume_m5,liquidity_usd,raw_json
        FROM snapshots WHERE pair_address=?1 AND token_address=?2 AND observed_at>=?3 AND observed_at<?4
        ORDER BY observed_at DESC LIMIT 4097")?;
    let rows = query
        .query_map(params![pair, token, from, until], |row| {
            Ok((
                row.get::<_, String>(0)?,
                [row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?],
                row.get::<_, String>(5)?,
            ))
        })?
        .collect::<std::result::Result<Vec<_>, _>>()?;
    if rows.len() > 4096 {
        return Err("shadow history limit exceeded".into());
    }
    Ok(rows
        .into_iter()
        .filter_map(|(at, values, raw)| {
            let raw = serde_json::from_str(&raw).ok()?;
            let sample = decode(pair, &at, values, &raw)?;
            sample.price.filter(|p| *p > 0.0)?;
            Some(sample)
        })
        .collect())
}

fn change_key(value: &Value) -> Value {
    let mut key = serde_json::Map::new();
    for name in [
        "first_seen_at",
        "stage",
        "data_status",
        "risk_status",
        "terminated",
        "expired",
        "c_closed",
        "c_reason",
        "settings",
        "experiment_version",
    ] {
        key.insert(name.into(), value[name].clone());
    }
    Value::Object(key)
}

fn summary(state: &State, settings: &Settings) -> Value {
    let mut value = signal_v2::summary(state);
    value["experiment_version"] = json!(EXPERIMENT_VERSION);
    value["settings"] = json!(settings);
    value
}

pub fn save(
    conn: &mut Connection,
    state: &State,
    consumed: &str,
    settings: &Settings,
) -> Result<bool> {
    let summary = summary(state, settings);
    let parameters = json!({"experiment_version":EXPERIMENT_VERSION,"settings":settings});
    let tx = conn.transaction()?;
    let prior: Option<String> = tx
        .query_row(
            "SELECT summary_json FROM signal_v2_state WHERE token_address=?1",
            [&state.token_address],
            |r| r.get(0),
        )
        .optional()?;
    let changed = prior
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .is_none_or(|old| change_key(&old) != change_key(&summary));
    tx.execute("INSERT INTO signal_v2_state(token_address,pair_address,first_seen_at,updated_at,last_consumed_at,state_json,summary_json,settings_json)
        VALUES (?1,?2,?3,?4,?5,?6,?7,?8) ON CONFLICT(token_address) DO UPDATE SET
        pair_address=excluded.pair_address,first_seen_at=excluded.first_seen_at,updated_at=excluded.updated_at,
        last_consumed_at=excluded.last_consumed_at,state_json=excluded.state_json,summary_json=excluded.summary_json,settings_json=excluded.settings_json",
        params![state.token_address,state.pair_address,state.first_seen_at,state.updated_at,consumed,serde_json::to_string(state)?,summary.to_string(),parameters.to_string()])?;
    if changed {
        tx.execute("INSERT INTO signal_v2_changes(token_address,pair_address,recorded_at,summary_json) VALUES (?1,?2,?3,?4)",
            params![state.token_address,state.pair_address,state.updated_at,summary.to_string()])?;
    }
    tx.commit()?;
    Ok(changed)
}

pub fn load(conn: &Connection, token: &str) -> Result<Option<(State, String, Value)>> {
    let row: Option<(String,String,String)> = conn.query_row("SELECT state_json,last_consumed_at,settings_json FROM signal_v2_state WHERE token_address=?1", [token], |r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?;
    row.map(|(raw, consumed, parameters)| {
        let state: State = serde_json::from_str(&raw)?;
        if state.token_address != token || !state.is_valid_for_restore() {
            return Err("invalid shadow state".into());
        }
        chrono::DateTime::parse_from_rfc3339(&consumed)?;
        Ok((state, consumed, serde_json::from_str(&parameters)?))
    })
    .transpose()
}

fn cancel(state: &mut State, now: i64, settings: &Settings) {
    // The public source-mismatch path cancels private pending attempts without reconstructing State.
    let invalid = Sample {
        at: now,
        pair_address: String::new(),
        price: None,
        volume_h1: None,
        volume_m5: None,
        liquidity_usd: None,
        fingerprint: String::new(),
    };
    signal_v2::advance(state, &invalid, &[], 0, &[], settings);
    state.data_status = "interrupted".into();
    if state.b_at.is_some() {
        state.c_reason = Some("interrupted".into());
    }
}

pub fn observe(
    path: &str,
    token: &str,
    samples: &[Sample],
    at: &str,
    score: i32,
    risks: &[String],
    settings: &Settings,
) -> Result<Option<Value>> {
    if !settings_valid(settings) {
        crate::logging::event(
            "warn",
            "signal_v2_invalid_settings",
            json!({"reason":"invalid_min_volume_h1_usd"}),
        );
        return Err("invalid shadow settings".into());
    }
    let now = chrono::DateTime::parse_from_rfc3339(at)?.timestamp();
    let mut conn = connect(path)?;
    let loaded = match load(&conn, token) {
        Ok(value) => value,
        Err(_) => {
            crate::logging::event(
                "warn",
                "signal_v2_state_reset",
                json!({"token":token,"reason":"invalid_or_unavailable_state"}),
            );
            None
        }
    };
    let parameters = json!({"experiment_version":EXPERIMENT_VERSION,"settings":settings});
    let config_changed = loaded
        .as_ref()
        .is_some_and(|(_, _, stored)| *stored != parameters);
    let prior_pair = loaded
        .as_ref()
        .map(|(state, _, _)| state.pair_address.clone());
    let (mut state, consumed) = if let Some((state, consumed, _)) =
        loaded.filter(|(state, _, _)| {
            now.saturating_sub(state.first_seen_at) < 7 * 86400 && !config_changed
        }) {
        (state, Some(consumed))
    } else {
        let first = samples
            .iter()
            .filter(|s| {
                s.price.is_some_and(|p| p.is_finite() && p > 0.0)
                    && s.liquidity_usd.is_some_and(|l| l.is_finite() && l >= 0.0)
            })
            .max_by(|a, b| {
                a.liquidity_usd
                    .unwrap()
                    .total_cmp(&b.liquidity_usd.unwrap())
                    .then_with(|| b.pair_address.cmp(&a.pair_address))
            })
            .cloned()
            .or_else(|| {
                prior_pair
                    .filter(|_| config_changed)
                    .map(|pair_address| Sample {
                        at: now,
                        pair_address,
                        price: None,
                        volume_h1: None,
                        volume_m5: None,
                        liquidity_usd: None,
                        fingerprint: String::new(),
                    })
            });
        let Some(first) = first else {
            return Ok(None);
        };
        (State::new(token, &first), None)
    };
    if let Some(consumed) = &consumed {
        let unseen: bool = match conn.query_row("SELECT EXISTS(SELECT 1 FROM snapshots WHERE pair_address=?1 AND token_address=?2 AND observed_at>?3 AND observed_at<?4)",params![state.pair_address,token,consumed,at],|r|r.get(0)) {
            Ok(value) => value,
            Err(error) => { cancel(&mut state,now,settings); save(&mut conn,&state,at,settings)?; return Err(error.into()); }
        };
        if unseen {
            cancel(&mut state, now, settings);
            crate::logging::event(
                "warn",
                "signal_v2_continuity_reset",
                json!({"token":token,"reason":"unconsumed_snapshot"}),
            );
        }
    }
    signal_v2::tick(&mut state, now);
    if let Some(current) = samples
        .iter()
        .find(|s| s.pair_address == state.pair_address)
    {
        let history = match history(&conn, token, &state.pair_address, now) {
            Ok(history) => history,
            Err(error) => {
                cancel(&mut state, now, settings);
                save(&mut conn, &state, at, settings)?;
                return Err(error);
            }
        };
        if current.fingerprint.is_empty()
            || !current.price.is_some_and(|p| p.is_finite() && p > 0.0)
            || [current.volume_h1, current.volume_m5, current.liquidity_usd]
                .iter()
                .any(|v| v.is_some_and(|v| !v.is_finite() || v < 0.0))
        {
            cancel(&mut state, now, settings);
        }
        signal_v2::advance(&mut state, current, &history, score, risks, settings);
    }
    if config_changed {
        state.data_status = "config_changed".into();
    }
    let changed = save(&mut conn, &state, at, settings)?;
    let summary = summary(&state, settings);
    crate::logging::event(
        "info",
        "signal_v2_observed",
        json!({"token":token,"stage":state.stage,"data_status":state.data_status,"risk_status":state.risk_status,"changed":changed,"warmup":state.data_status=="warming_up","gap":state.data_status=="interrupted","reference_missing":!samples.iter().any(|s|s.pair_address==state.pair_address)}),
    );
    Ok(Some(summary))
}

pub fn summaries(path: &str, token: Option<&str>, limit: usize, now: i64) -> Result<Vec<Value>> {
    let conn = Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    conn.busy_timeout(std::time::Duration::from_millis(100))?;
    let sql = if token.is_some() {
        "SELECT state_json,settings_json FROM signal_v2_state WHERE token_address=?1 ORDER BY updated_at DESC LIMIT ?2"
    } else {
        "SELECT state_json,settings_json FROM signal_v2_state ORDER BY updated_at DESC LIMIT ?1"
    };
    let mut query = conn.prepare(sql)?;
    let read = |row: &rusqlite::Row<'_>| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?));
    let raw = if let Some(token) = token {
        query
            .query_map(params![token, limit], read)?
            .collect::<std::result::Result<Vec<_>, _>>()?
    } else {
        query
            .query_map([limit], read)?
            .collect::<std::result::Result<Vec<_>, _>>()?
    };
    raw.into_iter()
        .map(|(raw, parameters)| {
            let mut state: State = serde_json::from_str(&raw)?;
            if !state.is_valid_for_restore() {
                return Err("invalid shadow state".into());
            }
            let parameters: Value = serde_json::from_str(&parameters)?;
            let settings: Settings = serde_json::from_value(parameters["settings"].clone())?;
            signal_v2::tick(&mut state, now);
            let mut value = summary(&state, &settings);
            value["experiment_version"] = parameters["experiment_version"].clone();
            Ok(value)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn database() -> String {
        let path = std::env::temp_dir().join(format!(
            "tmm-v2-{}-{}.db",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let path = path.to_str().unwrap().to_string();
        crate::db::init_schema(&path).unwrap();
        path
    }
    fn at(time: i64) -> String {
        chrono::DateTime::from_timestamp(time, 0)
            .unwrap()
            .to_rfc3339()
    }
    fn observation(time: i64, price: f64) -> Sample {
        Sample {
            at: time,
            pair_address: "pool".into(),
            price: Some(price),
            volume_h1: Some(30000.0),
            volume_m5: Some(3000.0),
            liquidity_usd: Some(50000.0),
            fingerprint: format!("request-{time}"),
        }
    }
    fn insert(conn: &Connection, time: i64, pair: &str, raw: &Value, price: f64) {
        conn.execute("INSERT INTO snapshots(pair_address,token_address,observed_at,price_usd,liquidity_usd,volume_h1,volume_m5,raw_json) VALUES (?1,'token',?2,?3,50000,30000,0,?4)", params![pair,at(time),price,raw.to_string()]).unwrap();
    }
    fn raw(time: i64) -> Value {
        json!({"signal_v2":{"version":1,"provider":"dexscreener","request_id":format!("r{time}"),"request_completed_at":at(time),"source_time_unverified":true,"presence":{"price_usd":true,"liquidity_usd":true,"volume_h1":true,"volume_m5":false}}})
    }

    #[test]
    fn signal_v2_metadata_preserves_absence_without_copying_response() {
        let value = metadata(
            &json!({"priceUsd":"1","volume":{"h1":0},"liquidity":{"usd":12},"secret":"never-copy"}),
            &at(100),
        );
        assert_eq!(value["signal_v2"]["presence"]["volume_h1"], true);
        assert_eq!(value["signal_v2"]["presence"]["volume_m5"], false);
        assert_eq!(value["signal_v2"]["provider"], "dexscreener");
        assert!(!value.to_string().contains("never-copy"));
    }
    #[test]
    fn signal_v2_history_rejects_legacy_missing_and_wrong_provider_metadata() {
        let path = database();
        let conn = Connection::open(&path).unwrap();
        insert(&conn, 100, "pool", &json!({}), 1.0);
        insert(&conn, 101, "pool", &raw(101), 2.0);
        let mut wrong = raw(102);
        wrong["signal_v2"]["provider"] = json!("other");
        insert(&conn, 102, "pool", &wrong, 3.0);
        let mut missing = raw(103);
        missing["signal_v2"]["presence"]
            .as_object_mut()
            .unwrap()
            .remove("volume_h1");
        insert(&conn, 103, "pool", &missing, 4.0);
        insert(&conn, 104, "other", &raw(104), 5.0);
        insert(&conn, 90000, "pool", &raw(90000), 6.0);
        let rows = history(&conn, "token", "pool", 200).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].at, 101);
        assert_eq!(rows[0].volume_h1, Some(30000.0));
        assert_eq!(rows[0].volume_m5, None);
    }
    #[test]
    fn signal_v2_state_roundtrip_and_changes_ignore_polling_evidence() {
        let path = database();
        let mut conn = Connection::open(&path).unwrap();
        let first = observation(100, 1.0);
        let mut state = State::new("token", &first);
        signal_v2::advance(&mut state, &first, &[], 65, &[], &Settings::default());
        assert!(save(&mut conn, &state, &at(100), &Settings::default()).unwrap());
        let restored = load(&conn, "token").unwrap().unwrap();
        assert_eq!(
            serde_json::to_value(&restored.0).unwrap(),
            serde_json::to_value(&state).unwrap()
        );
        assert_eq!(restored.1, at(100));
        let next = observation(160, 1.01);
        signal_v2::advance(&mut state, &next, &[], 65, &[], &Settings::default());
        assert!(!save(&mut conn, &state, &at(160), &Settings::default()).unwrap());
        state.risk_status = "WARN".into();
        assert!(save(&mut conn, &state, &at(160), &Settings::default()).unwrap());
        assert_eq!(
            conn.query_row("SELECT COUNT(*) FROM signal_v2_changes", [], |r| r
                .get::<_, i64>(0))
                .unwrap(),
            2
        );
    }
    #[test]
    fn signal_v2_cleanup_uses_original_event_time_and_summary_ticks_without_writes() {
        let path = database();
        let mut conn = Connection::open(&path).unwrap();
        let now = chrono::Utc::now().timestamp();
        let first = observation(now - 8 * 86400, 1.0);
        let mut state = State::new("token", &first);
        state.updated_at = now;
        save(&mut conn, &state, &at(now), &Settings::default()).unwrap();
        let rows = summaries(&path, Some("token"), 10, now).unwrap();
        assert_eq!(rows[0]["expired"], true);
        assert!(!load(&conn, "token").unwrap().unwrap().0.expired);
        crate::db::prune_history(&path).unwrap();
        assert!(load(&conn, "token").unwrap().is_none());
    }
    #[test]
    fn signal_v2_unconsumed_snapshot_cancels_pending_confirmation_on_restart() {
        let path = database();
        let mut conn = Connection::open(&path).unwrap();
        let first = observation(1000, 100.0);
        let mut state = State::new("token", &first);
        signal_v2::advance(&mut state, &first, &[], 65, &[], &Settings::default());
        // Model a pending B from a prior successful process without exposing engine internals.
        let mut serialized = serde_json::to_value(&state).unwrap();
        serialized["b_pending"] = json!({"at":1000,"count":1});
        serialized["b_rearmed"] = json!(true);
        serialized["reference_upper"] = json!(100.0);
        serialized["b_crossed_at"] = json!(1000);
        state = serde_json::from_value(serialized).unwrap();
        save(&mut conn, &state, &at(1000), &Settings::default()).unwrap();
        insert(&conn, 1060, "pool", &raw(1060), 90.0);
        observe(
            &path,
            "token",
            &[observation(1240, 102.0)],
            &at(1240),
            65,
            &[],
            &Settings::default(),
        )
        .unwrap();
        let value = serde_json::to_value(load(&conn, "token").unwrap().unwrap().0).unwrap();
        assert!(value["b_pending"].is_null());
        assert_eq!(value["b_rearmed"], false);
        assert!(value["b_at"].is_null());
    }
    #[test]
    fn signal_v2_parameter_changes_restart_at_a_without_reusing_b() {
        let path = database();
        let first = observation(1000, 100.0);
        let old = Settings::default();
        observe(&path, "token", &[first], &at(1000), 65, &[], &old).unwrap();
        let conn = Connection::open(&path).unwrap();
        let mut value: Value = serde_json::from_str(
            &conn
                .query_row("SELECT state_json FROM signal_v2_state", [], |r| {
                    r.get::<_, String>(0)
                })
                .unwrap(),
        )
        .unwrap();
        value["b_at"] = json!(1000);
        value["b_price"] = json!(100.0);
        value["first_wave_high"] = json!(100.0);
        value["reference_upper"] = json!(99.0);
        value["stage"] = json!("first_start");
        conn.execute(
            "UPDATE signal_v2_state SET state_json=?1",
            [value.to_string()],
        )
        .unwrap();
        let settings = Settings {
            min_volume_h1_usd: 20000.0,
            focus_score_threshold: 60,
        };
        let result = observe(
            &path,
            "token",
            &[observation(1060, 110.0)],
            &at(1060),
            65,
            &[],
            &settings,
        )
        .unwrap()
        .unwrap();
        assert_eq!(result["stage"], "watch");
        assert!(result["b_at"].is_null());
        assert_eq!(result["data_status"], "config_changed");
    }

    #[test]
    fn signal_v2_history_read_failure_persists_canceled_confirmation() {
        let path = database();
        let mut conn = Connection::open(&path).unwrap();
        let first = observation(1000, 100.0);
        let mut state = State::new("token", &first);
        signal_v2::advance(&mut state, &first, &[], 65, &[], &Settings::default());
        let mut serialized = serde_json::to_value(state).unwrap();
        serialized["b_pending"] = json!({"at":1000,"count":1});
        serialized["reference_upper"] = json!(100.0);
        let state: State = serde_json::from_value(serialized).unwrap();
        save(&mut conn, &state, &at(1000), &Settings::default()).unwrap();
        conn.execute("DROP TABLE snapshots", []).unwrap();
        assert!(observe(
            &path,
            "token",
            &[observation(1060, 102.0)],
            &at(1060),
            65,
            &[],
            &Settings::default()
        )
        .is_err());
        let state = load(&conn, "token").unwrap().unwrap().0;
        assert!(serde_json::to_value(state).unwrap()["b_pending"].is_null());
    }
    #[test]
    fn signal_v2_save_failure_leaves_old_checkpoint_and_cancels_on_next_process() {
        let path = database();
        let mut conn = Connection::open(&path).unwrap();
        let first = observation(1000, 100.0);
        let mut state = State::new("token", &first);
        signal_v2::advance(&mut state, &first, &[], 65, &[], &Settings::default());
        let mut serialized = serde_json::to_value(state).unwrap();
        serialized["b_pending"] = json!({"at":1000,"count":1});
        serialized["b_rearmed"] = json!(true);
        serialized["reference_upper"] = json!(100.0);
        let state: State = serde_json::from_value(serialized).unwrap();
        save(&mut conn, &state, &at(1000), &Settings::default()).unwrap();
        insert(&conn, 1060, "pool", &raw(1060), 90.0);
        conn.execute_batch("CREATE TRIGGER reject_shadow BEFORE INSERT ON signal_v2_state BEGIN SELECT RAISE(ABORT,'test interrupted save'); END;").unwrap();
        assert!(observe(
            &path,
            "token",
            &[observation(1060, 90.0)],
            &at(1060),
            65,
            &[],
            &Settings::default()
        )
        .is_err());
        assert_eq!(load(&conn, "token").unwrap().unwrap().1, at(1000));
        conn.execute("DROP TRIGGER reject_shadow", []).unwrap();
        observe(
            &path,
            "token",
            &[observation(1240, 102.0)],
            &at(1240),
            65,
            &[],
            &Settings::default(),
        )
        .unwrap();
        let state = serde_json::to_value(load(&conn, "token").unwrap().unwrap().0).unwrap();
        assert!(state["b_pending"].is_null());
        assert_eq!(state["b_rearmed"], false);
        assert!(state["b_at"].is_null());
    }

    #[test]
    fn signal_v2_invalid_current_sample_cancels_private_pending_confirmation() {
        let path = database();
        let mut conn = Connection::open(&path).unwrap();
        let first = observation(1000, 100.0);
        let mut state = State::new("token", &first);
        signal_v2::advance(&mut state, &first, &[], 65, &[], &Settings::default());
        let mut serialized = serde_json::to_value(state).unwrap();
        serialized["b_pending"] = json!({"at":1000,"count":1});
        serialized["reference_upper"] = json!(100.0);
        save(
            &mut conn,
            &serde_json::from_value(serialized).unwrap(),
            &at(1000),
            &Settings::default(),
        )
        .unwrap();
        let mut invalid = observation(1060, 102.0);
        invalid.fingerprint.clear();
        observe(
            &path,
            "token",
            &[invalid],
            &at(1060),
            65,
            &[],
            &Settings::default(),
        )
        .unwrap();
        let state = serde_json::to_value(load(&conn, "token").unwrap().unwrap().0).unwrap();
        assert!(state["b_pending"].is_null());
        assert_eq!(state["b_rearmed"], false);
    }
    #[test]
    fn signal_v2_structurally_invalid_state_resets_without_panicking() {
        let path = database();
        let mut conn = Connection::open(&path).unwrap();
        let first = observation(1000, 100.0);
        let mut state = State::new("token", &first);
        signal_v2::advance(&mut state, &first, &[], 65, &[], &Settings::default());
        state.b_at = Some(1000);
        state.reference_upper = Some(99.0);
        save(&mut conn, &state, &at(1000), &Settings::default()).unwrap();
        let result = observe(
            &path,
            "token",
            &[observation(1060, 102.0)],
            &at(1060),
            65,
            &[],
            &Settings::default(),
        )
        .unwrap()
        .unwrap();
        assert_eq!(result["stage"], "watch");
        assert!(result["b_at"].is_null());
    }
    #[test]
    fn signal_v2_rejects_nonfinite_and_nonpositive_thresholds_without_writing_state() {
        let path = database();
        for minimum in [f64::NAN, f64::INFINITY, -1.0, 0.0] {
            let settings = Settings {
                min_volume_h1_usd: minimum,
                ..Settings::default()
            };
            assert!(observe(
                &path,
                "token",
                &[observation(1000, 100.0)],
                &at(1000),
                65,
                &[],
                &settings
            )
            .is_err());
        }
        assert!(summaries(&path, None, 10, 1000).unwrap().is_empty());
    }
    #[test]
    fn signal_v2_parameter_change_cancels_pending_even_without_a_usable_pool() {
        let path = database();
        let mut conn = Connection::open(&path).unwrap();
        let first = observation(1000, 100.0);
        let mut state = State::new("token", &first);
        signal_v2::advance(&mut state, &first, &[], 65, &[], &Settings::default());
        let mut serialized = serde_json::to_value(state).unwrap();
        serialized["b_pending"] = json!({"at":1000,"count":1});
        serialized["reference_upper"] = json!(100.0);
        save(
            &mut conn,
            &serde_json::from_value(serialized).unwrap(),
            &at(1000),
            &Settings::default(),
        )
        .unwrap();
        let settings = Settings {
            min_volume_h1_usd: 20000.0,
            ..Settings::default()
        };
        observe(&path, "token", &[], &at(1060), 65, &[], &settings).unwrap();
        let state = serde_json::to_value(load(&conn, "token").unwrap().unwrap().0).unwrap();
        assert!(state["b_pending"].is_null());
        assert_eq!(state["data_status"], "config_changed");
        assert_eq!(state["stage"], "inactive");
    }
    #[test]
    fn signal_v2_restored_b_pending_without_reference_resets_before_advancement() {
        let path = database();
        let mut conn = Connection::open(&path).unwrap();
        let first = observation(1000, 100.0);
        let mut state = State::new("token", &first);
        signal_v2::advance(&mut state, &first, &[], 65, &[], &Settings::default());
        let mut serialized = serde_json::to_value(state).unwrap();
        serialized["b_pending"] = json!({"at":1000,"count":1});
        let malformed: State = serde_json::from_value(serialized).unwrap();
        save(&mut conn, &malformed, &at(1000), &Settings::default()).unwrap();
        assert!(load(&conn, "token").is_err());
        let result = observe(
            &path,
            "token",
            &[observation(1060, 102.0)],
            &at(1060),
            65,
            &[],
            &Settings::default(),
        )
        .unwrap()
        .unwrap();
        assert_eq!(result["stage"], "watch");
        assert!(result["b_at"].is_null());
        let restored = serde_json::to_value(load(&conn, "token").unwrap().unwrap().0).unwrap();
        assert!(restored["b_pending"].is_null());
        assert_eq!(restored["last_evidence_at"], 1060);
    }
    #[test]
    fn signal_v2_restored_c_pending_requires_established_b_and_frozen_base() {
        for missing in [
            "b_at",
            "base_upper",
            "base_lower",
            "base_volume_m5",
            "base_frozen_at",
        ] {
            let path = database();
            let mut conn = Connection::open(&path).unwrap();
            let first = observation(1000, 100.0);
            let mut state = State::new("token", &first);
            signal_v2::advance(&mut state, &first, &[], 65, &[], &Settings::default());
            let mut serialized = serde_json::to_value(state).unwrap();
            serialized["b_at"] = json!(1000);
            serialized["b_price"] = json!(100.0);
            serialized["reference_upper"] = json!(99.0);
            serialized["first_wave_high"] = json!(112.0);
            serialized["base_upper"] = json!(106.0);
            serialized["base_lower"] = json!(105.0);
            serialized["base_volume_m5"] = json!(1000.0);
            serialized["base_frozen_at"] = json!(1000);
            serialized["c_pending"] = json!({"at":1000,"count":1});
            serialized["stage"] = json!("base");
            let complete: State = serde_json::from_value(serialized.clone()).unwrap();
            save(&mut conn, &complete, &at(1000), &Settings::default()).unwrap();
            assert!(load(&conn, "token").is_ok());
            serialized[missing] = Value::Null;
            save(
                &mut conn,
                &serde_json::from_value(serialized).unwrap(),
                &at(1000),
                &Settings::default(),
            )
            .unwrap();
            assert!(
                load(&conn, "token").is_err(),
                "accepted C pending without {missing}"
            );
            let result = observe(
                &path,
                "token",
                &[observation(1060, 107.0)],
                &at(1060),
                65,
                &[],
                &Settings::default(),
            )
            .unwrap()
            .unwrap();
            assert_eq!(result["stage"], "watch");
            assert!(result["b_at"].is_null());
            assert!(result["c_at"].is_null());
        }
    }
}
