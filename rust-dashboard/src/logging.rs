use serde_json::Value;

pub fn event(level: &str, event: &str, fields: Value) {
    let priority = |name: &str| match name.to_ascii_lowercase().as_str() {
        "error" => 1,
        "warn" => 2,
        "info" => 3,
        "debug" => 4,
        _ => 3,
    };
    if priority(level) > priority(&std::env::var("LOG_LEVEL").unwrap_or_else(|_| "info".into())) {
        return;
    }
    eprintln!(
        "{}",
        serde_json::json!({
            "at": chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            "level": level,
            "event": event,
            "fields": fields,
        })
    );
}
