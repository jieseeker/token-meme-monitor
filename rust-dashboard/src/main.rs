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

    if mode == "signal-v2-replay" {
        let config = config::Config::from_env();
        let settings = engine::signal_v2::Settings {
            min_volume_h1_usd: config.min_volume_h1_usd,
            focus_score_threshold: config.focus_score_threshold,
        };
        match engine::signal_v2_replay::run(&db_path, &args[2..], &settings) {
            Ok(report) => println!("{}", report),
            Err(error) => {
                eprintln!("signal-v2-replay: {error}");
                std::process::exit(1);
            }
        }
        return;
    }

    if mode == "factor-v1-replay" {
        match engine::factor_v1_replay::replay(&db_path) {
            Ok(report) => println!("{}", report),
            Err(error) => {
                eprintln!("factor-v1-replay: {error}");
                std::process::exit(1);
            }
        }
        return;
    }

    if mode == "alpha-discovery-audit" {
        let top_n = args
            .get(2)
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(20);
        match engine::alpha_discovery::audit(&db_path, top_n) {
            Ok(report) => println!("{report}"),
            Err(error) => {
                eprintln!("alpha-discovery-audit: {error}");
                std::process::exit(1);
            }
        }
        return;
    }

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
