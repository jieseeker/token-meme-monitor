mod clients;
mod config;
mod dashboard;
mod db;
mod depth;
mod engine;
mod logging;

use std::env;

#[tokio::main]
async fn main() {
    let args: Vec<String> = env::args().collect();
    let mode = args.get(1).map(|s| s.as_str()).unwrap_or("all");

    let db_path =
        env::var("MONITOR_DATABASE_PATH").unwrap_or_else(|_| "data/monitor.db".to_string());

    // Ensure parent dir
    if let Some(parent) = std::path::Path::new(&db_path).parent() {
        let _ = std::fs::create_dir_all(parent);
    }

    logging::event(
        "info",
        "process_started",
        serde_json::json!({"mode":mode,"database":db_path}),
    );

    match mode {
        "worker" => {
            engine::worker::run_worker(&db_path).await;
        }
        "dashboard" => {
            dashboard::serve(&db_path).await;
        }
        "init-db" => {
            db::init_schema(&db_path).expect("failed to init schema");
            logging::event(
                "info",
                "database_initialized",
                serde_json::json!({"database":db_path}),
            );
        }
        _ => {
            // "all": run market worker and dashboard enrichment concurrently.
            let db2 = db_path.clone();
            tokio::select! {
                _ = engine::worker::run_worker(&db_path) => logging::event("error", "worker_exited", serde_json::json!({})),
                _ = dashboard::serve(&db2) => logging::event("error", "dashboard_exited", serde_json::json!({})),
            }
        }
    }
}
