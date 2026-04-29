from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import replace
from datetime import timedelta
from pprint import pprint

from token_meme_monitor.config import load_config
from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.features import build_feature_vector
from token_meme_monitor.indicator_candidates import compute_candidate_indicators
from token_meme_monitor.logging_config import configure_logging
from token_meme_monitor.market_data import build_alpha_reference, sanitize_alpha_metadata, sanitize_pair_snapshot
from token_meme_monitor.models import PairSnapshot, SignalDecision
from token_meme_monitor.prediction_outcomes import compute_prediction_outcome_with_hourly_ohlcv
from token_meme_monitor.predictions import build_prediction_calibration, build_prediction_result
from token_meme_monitor.signals import SignalEngine
from token_meme_monitor.utils import json_loads, parse_datetime, utcnow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Token Meme Monitor")
    parser.add_argument("--env-file", default=".env", help="Path to env file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create database schema")
    subparsers.add_parser("print-config", help="Print resolved runtime config")
    health_report = subparsers.add_parser("health-report", help="Print backend health and data quality counters")
    health_report.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    compact_history = subparsers.add_parser("compact-history", help="Compact cold snapshot and signal history")
    compact_history.add_argument("--older-than-days", type=int, default=14, help="Compact rows older than this many days")
    compact_history.add_argument("--before", default=None, help="Explicit UTC cutoff timestamp, e.g. 2026-04-02T00:00:00+00:00")
    compact_history.add_argument("--dry-run", action="store_true", help="Only print estimated rows and bytes")
    compact_history.add_argument("--execute", action="store_true", help="Mutate the database; without this flag the command is a dry-run")
    compact_history.add_argument("--batch-size", type=int, default=5000, help="Rows to archive per batch")
    compact_history.add_argument("--vacuum", action="store_true", help="Run VACUUM after execute to reclaim SQLite file space")
    healthcheck = subparsers.add_parser("healthcheck", help="Check whether the configured RPC supports discovery")
    healthcheck.add_argument(
        "--log-span",
        type=int,
        default=2,
        help="Number of recent blocks to probe with eth_getLogs",
    )

    run_worker = subparsers.add_parser("run-worker", help="Run the worker loop")
    run_worker.add_argument("--once", action="store_true", help="Run one cycle and exit")

    subparsers.add_parser("cleanup-data", help="Clean existing token metadata and snapshots for bad numeric values")
    validate_tokens = subparsers.add_parser("validate-token-list", help="Run historical validation for tokens in token_list.txt")
    validate_tokens.add_argument(
        "--input",
        default="token_meme_monitor/token_list.txt",
        help="Path to token list file",
    )
    validate_tokens.add_argument(
        "--json-out",
        default="data/backtests/token_list_validation.json",
        help="Path to JSON output",
    )
    validate_tokens.add_argument(
        "--md-out",
        default="data/backtests/token_list_validation.md",
        help="Path to Markdown report output",
    )
    export_predictions = subparsers.add_parser("export-prediction-dataset", help="Export signal prediction training dataset")
    export_predictions.add_argument(
        "--output",
        default="data/backtests/prediction_dataset.csv",
        help="Path to CSV output",
    )
    export_predictions.add_argument("--limit", type=int, default=None, help="Optional max rows to export")
    refresh_prediction_outcomes = subparsers.add_parser(
        "refresh-prediction-outcomes",
        help="Compute stored prediction outcomes from cached or external hourly OHLCV",
    )
    refresh_prediction_outcomes.add_argument("--limit", type=int, default=1000, help="Max prediction outcomes to compute")
    refresh_prediction_outcomes.add_argument(
        "--refresh-missing-quality",
        action="store_true",
        help="Also recompute mature outcomes written before outcome-quality fields existed",
    )
    rebuild_predictions = subparsers.add_parser("rebuild-predictions", help="Recompute stored signal predictions")
    rebuild_predictions.add_argument("--limit", type=int, default=None, help="Optional max rows to recompute")
    backtest_predictions = subparsers.add_parser(
        "backtest-predictions",
        help="Run an expanding walk-forward backtest for stored signal predictions",
    )
    backtest_predictions.add_argument(
        "--json-out",
        default="data/backtests/prediction_backtest.json",
        help="Path to JSON report output",
    )
    backtest_predictions.add_argument(
        "--md-out",
        default="data/backtests/prediction_backtest.md",
        help="Path to Markdown report output",
    )
    backtest_predictions.add_argument("--limit", type=int, default=None, help="Optional max dataset rows to load")
    backtest_predictions.add_argument("--train-ratio", type=float, default=0.70, help="Initial chronological train split")
    backtest_predictions.add_argument(
        "--max-price-divergence-pct",
        type=float,
        default=None,
        help="Optional absolute price-source divergence filter, e.g. 0.10 for 10%%",
    )
    scheduled_backtest = subparsers.add_parser(
        "scheduled-backtest-report",
        help="Refresh mature outcomes and write a scheduled prediction backtest report",
    )
    scheduled_backtest.add_argument(
        "--json-out",
        default="data/backtests/scheduled/latest.json",
        help="Path to latest JSON report output",
    )
    scheduled_backtest.add_argument(
        "--md-out",
        default="data/backtests/scheduled/latest.md",
        help="Path to latest Markdown report output",
    )
    scheduled_backtest.add_argument(
        "--archive-dir",
        default="data/backtests/scheduled",
        help="Directory for timestamped report copies",
    )
    scheduled_backtest.add_argument("--no-archive", action="store_true", help="Only write latest outputs")
    scheduled_backtest.add_argument("--limit", type=int, default=None, help="Optional max dataset rows to load")
    scheduled_backtest.add_argument("--train-ratio", type=float, default=0.70, help="Initial chronological train split")
    scheduled_backtest.add_argument(
        "--max-price-divergence-pct",
        type=float,
        default=0.10,
        help="Absolute price-source divergence filter, e.g. 0.10 for 10%%",
    )
    scheduled_backtest.add_argument("--top-gainers-limit", type=int, default=20, help="Number of top gainers to list")
    scheduled_backtest.add_argument(
        "--strong-gainer-return-threshold",
        type=float,
        default=0.20,
        help="Return threshold for miss analysis, e.g. 0.20 for +20%%",
    )
    scheduled_backtest.add_argument(
        "--refresh-outcome-limit",
        type=int,
        default=1000,
        help="Max mature prediction outcomes to refresh before reporting",
    )
    scheduled_backtest.add_argument(
        "--refresh-missing-quality",
        action="store_true",
        help="Also recompute mature outcomes written before outcome-quality fields existed",
    )
    scheduled_backtest.add_argument(
        "--skip-refresh-outcomes",
        action="store_true",
        help="Skip external outcome refresh and only analyze local stored rows",
    )
    scheduled_worker = subparsers.add_parser(
        "run-scheduled-backtest-worker",
        help="Run the scheduled backtest report worker loop inside this project",
    )
    scheduled_worker.add_argument("--once", action="store_true", help="Run one scheduled report cycle and exit")
    scheduled_worker.add_argument(
        "--interval-seconds",
        type=int,
        default=4 * 60 * 60,
        help="Seconds between scheduled report cycles",
    )
    scheduled_worker.add_argument(
        "--json-out",
        default="data/backtests/scheduled/latest.json",
        help="Path to latest JSON report output",
    )
    scheduled_worker.add_argument(
        "--md-out",
        default="data/backtests/scheduled/latest.md",
        help="Path to latest Markdown report output",
    )
    scheduled_worker.add_argument(
        "--archive-dir",
        default="data/backtests/scheduled",
        help="Directory for timestamped report copies",
    )
    scheduled_worker.add_argument("--no-archive", action="store_true", help="Only write latest outputs")
    scheduled_worker.add_argument("--limit", type=int, default=None, help="Optional max dataset rows to load")
    scheduled_worker.add_argument("--train-ratio", type=float, default=0.70, help="Initial chronological train split")
    scheduled_worker.add_argument(
        "--max-price-divergence-pct",
        type=float,
        default=0.10,
        help="Absolute price-source divergence filter, e.g. 0.10 for 10%%",
    )
    scheduled_worker.add_argument("--top-gainers-limit", type=int, default=20, help="Number of top gainers to list")
    scheduled_worker.add_argument(
        "--strong-gainer-return-threshold",
        type=float,
        default=0.20,
        help="Return threshold for miss analysis, e.g. 0.20 for +20%%",
    )
    scheduled_worker.add_argument(
        "--refresh-outcome-limit",
        type=int,
        default=1000,
        help="Max mature prediction outcomes to refresh before reporting",
    )
    scheduled_worker.add_argument(
        "--refresh-missing-quality",
        action="store_true",
        help="Also recompute mature outcomes written before outcome-quality fields existed",
    )
    scheduled_worker.add_argument(
        "--skip-refresh-outcomes",
        action="store_true",
        help="Skip external outcome refresh and only analyze local stored rows",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.env_file)
    configure_logging(config.log_level)

    if args.command == "print-config":
        pprint(config)
        return 0

    if args.command == "health-report":
        from token_meme_monitor.health import build_health_report, render_health_report

        repo = MonitorRepository(config.database_path)
        repo.initialize()
        try:
            report = build_health_report(repo, database_path=config.database_path)
        finally:
            repo.close()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(render_health_report(report))
        return 0

    if args.command == "compact-history":
        cutoff = parse_datetime(args.before) if args.before else utcnow() - timedelta(days=args.older_than_days)
        if cutoff is None:
            parser.error("--before must be an ISO datetime")
        if args.batch_size <= 0:
            parser.error("--batch-size must be positive")
        dry_run = args.dry_run or not args.execute
        repo = MonitorRepository(config.database_path)
        repo.initialize()
        try:
            if dry_run:
                summary = repo.estimate_history_compaction(cutoff)
                mode = "dry-run"
            else:
                summary = repo.compact_history(cutoff, batch_size=args.batch_size)
                mode = "execute"
                if args.vacuum:
                    repo._conn.execute("VACUUM")
                    summary["vacuum"] = True
        finally:
            repo.close()
        if dry_run:
            print(
                "history compaction "
                f"mode={mode} before={summary['before']} "
                f"snapshots={summary['snapshot_rows']} "
                f"signals={summary['signal_rows']} "
                f"snapshot_raw_json_mb={summary['snapshot_raw_json_bytes'] / 1024 / 1024:.2f} "
                f"signal_feature_json_mb={summary['signal_feature_json_bytes'] / 1024 / 1024:.2f} "
                f"rollup_hours={summary['snapshot_hourly_rollup_rows']}"
            )
        else:
            print(
                "history compaction "
                f"mode={mode} before={summary['before']} "
                f"snapshots_compacted={summary['snapshot_rows_compacted']} "
                f"signals_compacted={summary['signal_rows_compacted']} "
                f"signals_recompacted={summary.get('signal_rows_recompacted', 0)} "
                f"rollup_hours={summary['snapshot_hourly_rollup_rows']} "
                f"vacuum={bool(summary.get('vacuum'))}"
            )
        return 0

    if args.command == "healthcheck":
        from token_meme_monitor.clients.bsc import BscPairDiscoveryClient

        client = BscPairDiscoveryClient(
            rpc_url=config.bsc_rpc_url,
            factory_address=config.factory_address,
            quote_tokens=config.quote_tokens,
        )
        try:
            report = client.healthcheck(
                confirmations=config.discovery_block_confirmations,
                log_span=args.log_span,
            )
        except Exception as exc:
            print(f"RPC healthcheck failed for {config.bsc_rpc_url}")
            print(f"error: {type(exc).__name__}: {exc}")
            return 1

        print(f"RPC healthcheck passed for {report['rpc_url']}")
        print(f"latest_safe_block: {report['latest_safe_block']}")
        print(
            "block_lookup: "
            f"ok={report['block_lookup']['ok']} "
            f"block={report['block_lookup']['block_number']} "
            f"timestamp={report['block_lookup']['timestamp']}"
        )
        print(
            "pair_logs: "
            f"ok={report['pair_logs']['ok']} "
            f"range={report['pair_logs']['from_block']}-{report['pair_logs']['to_block']} "
            f"log_count={report['pair_logs']['log_count']}"
        )
        return 0

    if args.command == "init-db":
        repo = MonitorRepository(config.database_path)
        repo.initialize()
        repo.close()
        print(f"initialized database at {config.database_path}")
        return 0

    if args.command == "cleanup-data":
        repo = MonitorRepository(config.database_path)
        repo.initialize()
        signal_engine = SignalEngine(config.signal)
        token_updates = 0
        snapshot_updates = 0
        signal_updates = 0
        pair_updates = 0
        try:
            metadata_changed_tokens: set[str] = set()
            for token_row in repo.list_tokens_for_cleanup():
                original_metadata = json_loads(token_row.get("metadata_json"), {})
                cleaned_metadata = sanitize_alpha_metadata(original_metadata)
                if cleaned_metadata != original_metadata:
                    repo.update_token_metadata(token_row["token_address"], cleaned_metadata)
                    metadata_changed_tokens.add(str(token_row["token_address"]))
                    token_updates += 1

            prediction_calibration = build_prediction_calibration(repo.list_prediction_dataset_rows())
            for snapshot_row in repo.list_snapshots_for_cleanup():
                pair_created_at = parse_datetime(snapshot_row.get("pair_created_at")) or parse_datetime(snapshot_row.get("observed_at")) or utcnow()
                observed_at = parse_datetime(snapshot_row.get("observed_at")) or utcnow()
                snapshot = PairSnapshot(
                    pair_address=snapshot_row["pair_address"],
                    token_address=snapshot_row["token_address"],
                    token_symbol=snapshot_row.get("token_symbol") or "",
                    token_name=snapshot_row.get("token_name") or "",
                    quote_token_address=snapshot_row.get("quote_token_address") or "",
                    quote_symbol=snapshot_row.get("quote_symbol") or "",
                    observed_at=observed_at,
                    pair_created_at=pair_created_at,
                    dex_id=snapshot_row.get("dex_id") or "",
                    pair_url=(json_loads(snapshot_row.get("pair_metadata_json"), {}) or {}).get("pair_url", ""),
                    price_usd=snapshot_row.get("price_usd"),
                    price_native=snapshot_row.get("price_native"),
                    liquidity_usd=snapshot_row.get("liquidity_usd"),
                    fdv=snapshot_row.get("fdv"),
                    market_cap=snapshot_row.get("market_cap"),
                    volume_m5=snapshot_row.get("volume_m5") or 0.0,
                    volume_h1=snapshot_row.get("volume_h1") or 0.0,
                    volume_h24=snapshot_row.get("volume_h24") or 0.0,
                    buys_m5=int(snapshot_row.get("buys_m5") or 0),
                    sells_m5=int(snapshot_row.get("sells_m5") or 0),
                    buys_h1=int(snapshot_row.get("buys_h1") or 0),
                    sells_h1=int(snapshot_row.get("sells_h1") or 0),
                    price_change_m5=snapshot_row.get("price_change_m5") or 0.0,
                    price_change_h1=snapshot_row.get("price_change_h1") or 0.0,
                    price_change_h24=snapshot_row.get("price_change_h24") or 0.0,
                    website_count=int(snapshot_row.get("website_count") or 0),
                    social_count=int(snapshot_row.get("social_count") or 0),
                    boosts_active=int(snapshot_row.get("boosts_active") or 0),
                    raw_payload=json_loads(snapshot_row.get("raw_json"), {}),
                )
                raw_token_metadata = json_loads(snapshot_row.get("token_metadata_json"), {})
                token_metadata = sanitize_alpha_metadata(raw_token_metadata)
                cleaned_snapshot = sanitize_pair_snapshot(
                    snapshot,
                    monitor_universe=config.monitor_universe,
                    alpha_reference=build_alpha_reference(token_metadata),
                )
                snapshot_changed = cleaned_snapshot != snapshot
                metadata_changed = (
                    str(snapshot_row["token_address"]) in metadata_changed_tokens
                    or token_metadata != raw_token_metadata
                )
                if snapshot_changed or metadata_changed:
                    features = build_feature_vector(
                        cleaned_snapshot,
                        config.signal,
                        monitor_universe=config.monitor_universe,
                    )
                    if snapshot_changed:
                        repo.update_snapshot_cleaned(
                            int(snapshot_row["id"]),
                            snapshot=cleaned_snapshot,
                            age_minutes=features.age_minutes,
                            risk_flags=list(features.risk_flags),
                        )
                        snapshot_updates += 1
                    recent_history = repo.list_snapshot_context(
                        cleaned_snapshot.pair_address,
                        cleaned_snapshot.observed_at - timedelta(hours=72),
                    )
                    candidate_indicators = compute_candidate_indicators(
                        observed_at=cleaned_snapshot.observed_at,
                        price_usd=cleaned_snapshot.price_usd,
                        volume_h1=cleaned_snapshot.volume_h1,
                        market_cap=cleaned_snapshot.market_cap,
                        fdv=cleaned_snapshot.fdv,
                        history_rows=recent_history,
                    )
                    decision = signal_engine.evaluate(
                        features,
                        observed_at=cleaned_snapshot.observed_at,
                        monitor_universe=config.monitor_universe,
                        token_metadata=token_metadata,
                    )
                    decision = replace(
                        decision,
                        features={
                            **decision.features,
                            **candidate_indicators,
                        },
                    )
                    signal_id = repo.insert_signal(cleaned_snapshot.pair_address, cleaned_snapshot.token_address, decision)
                    prediction = build_prediction_result(
                        decision,
                        token_metadata=token_metadata,
                        calibration=prediction_calibration,
                    )
                    repo.upsert_signal_prediction(
                        signal_id,
                        pair_address=cleaned_snapshot.pair_address,
                        token_address=cleaned_snapshot.token_address,
                        observed_at=cleaned_snapshot.observed_at,
                        prediction=prediction,
                    )
                    signal_updates += 1
                    if decision.pair_state == "archived":
                        next_refresh_at = None
                        active = False
                    elif decision.pair_state in {"focused", "alerted"}:
                        next_refresh_at = cleaned_snapshot.observed_at + timedelta(
                            seconds=config.signal.focus_poll_interval_seconds
                        )
                        active = True
                    else:
                        next_refresh_at = cleaned_snapshot.observed_at + timedelta(
                            seconds=config.signal.base_poll_interval_seconds
                        )
                        active = True
                    pair_metadata = json_loads(snapshot_row.get("pair_metadata_json"), {})
                    pair_metadata.update({"pair_url": cleaned_snapshot.pair_url, "token_name": cleaned_snapshot.token_name})
                    repo.update_pair_after_snapshot(
                        cleaned_snapshot.pair_address,
                        state=decision.pair_state,
                        dex_id=cleaned_snapshot.dex_id or None,
                        token_symbol=cleaned_snapshot.token_symbol or None,
                        token_name=cleaned_snapshot.token_name or None,
                        last_snapshot_at=cleaned_snapshot.observed_at,
                        next_refresh_at=next_refresh_at,
                        risk_flags=list(decision.risk_flags),
                        metadata=pair_metadata,
                        active=active,
                    )
                    pair_updates += 1
        finally:
            repo.close()
        print(
            f"cleanup completed at {utcnow().isoformat(timespec='seconds')} "
            f"(tokens_updated={token_updates}, snapshots_updated={snapshot_updates}, "
            f"signals_updated={signal_updates}, pairs_updated={pair_updates})"
        )
        return 0

    if args.command == "validate-token-list":
        from pathlib import Path

        from token_meme_monitor.token_validation import GeckoTerminalBacktester

        token_file = Path(args.input)
        tokens = [line.strip() for line in token_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        backtester = GeckoTerminalBacktester(network=config.chain_id, database_path=config.database_path)
        results = backtester.run_for_tokens(tokens)
        backtester.write_outputs(results, json_path=args.json_out, markdown_path=args.md_out)
        print(
            f"validation completed at {utcnow().isoformat(timespec='seconds')} "
            f"(tokens={len(results)}, json='{args.json_out}', markdown='{args.md_out}')"
        )
        return 0

    if args.command == "export-prediction-dataset":
        from pathlib import Path

        repo = MonitorRepository(config.database_path)
        repo.initialize()
        try:
            rows = repo.list_prediction_dataset_rows(limit=args.limit)
        finally:
            repo.close()
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = _prediction_dataset_fieldnames(rows)
        with output_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(_prediction_dataset_row(row, fieldnames))
        print(
            f"prediction dataset exported at {utcnow().isoformat(timespec='seconds')} "
            f"(rows={len(rows)}, output='{args.output}')"
        )
        return 0

    if args.command == "refresh-prediction-outcomes":
        repo = MonitorRepository(config.database_path)
        repo.initialize()
        now = utcnow()
        updated = 0
        skipped = 0
        try:
            rows = repo.list_predictions_needing_outcomes(
                now,
                limit=args.limit,
                include_missing_quality=args.refresh_missing_quality,
            )
            for row in rows:
                observed_at = parse_datetime(row.get("observed_at"))
                if observed_at is None:
                    continue
                outcome = compute_prediction_outcome_with_hourly_ohlcv(
                    repo,
                    pair_address=row["pair_address"],
                    observed_at=observed_at,
                    feature_json=row.get("feature_json"),
                    network=config.chain_id,
                    now=now,
                )
                if outcome is None:
                    skipped += 1
                    continue
                repo.upsert_prediction_outcome(int(row["signal_id"]), outcome, evaluated_at=now)
                updated += 1
        finally:
            repo.close()
        print(
            f"prediction outcomes refreshed at {now.isoformat(timespec='seconds')} "
            f"(rows={updated}, skipped={skipped}, limit={args.limit})"
        )
        return 0

    if args.command == "rebuild-predictions":
        repo = MonitorRepository(config.database_path)
        repo.initialize()
        updated = 0
        try:
            rows = repo.list_prediction_dataset_rows(limit=args.limit)
            prediction_calibration = build_prediction_calibration(rows)
            for row in rows:
                observed_at = parse_datetime(row.get("observed_at"))
                if observed_at is None:
                    continue
                decision = SignalDecision(
                    observed_at=observed_at,
                    strategy_version=row.get("strategy_version") or config.signal.strategy_version,
                    score=int(row.get("score") or 0),
                    pair_state=row.get("pair_state") or "watching",
                    should_alert=bool(int(row.get("should_alert") or 0)),
                    reasons=tuple(json_loads(row.get("reasons"), [])),
                    risk_flags=tuple(json_loads(row.get("risk_flags"), [])),
                    features=json_loads(row.get("feature_json"), {}) or {},
                )
                token_metadata = json_loads(row.get("token_metadata_json"), {}) or {}
                prediction = build_prediction_result(
                    decision,
                    token_metadata=token_metadata,
                    calibration=prediction_calibration,
                )
                repo.upsert_signal_prediction(
                    int(row["signal_id"]),
                    pair_address=row["pair_address"],
                    token_address=row["token_address"],
                    observed_at=observed_at,
                    prediction=prediction,
                )
                updated += 1
        finally:
            repo.close()
        print(
            f"predictions rebuilt at {utcnow().isoformat(timespec='seconds')} "
            f"(rows={updated}, calibration_rows={prediction_calibration.total_rows})"
        )
        return 0

    if args.command == "backtest-predictions":
        from token_meme_monitor.prediction_backtest import build_prediction_backtest_report, write_prediction_backtest_outputs

        repo = MonitorRepository(config.database_path)
        repo.initialize()
        try:
            rows = repo.list_prediction_dataset_rows(limit=args.limit)
        finally:
            repo.close()
        report = build_prediction_backtest_report(
            rows,
            train_ratio=args.train_ratio,
            max_price_divergence_pct=args.max_price_divergence_pct,
        )
        write_prediction_backtest_outputs(report, json_path=args.json_out, markdown_path=args.md_out)
        print(
            f"prediction backtest completed at {utcnow().isoformat(timespec='seconds')} "
            f"(rows={report['total_rows']}, usable_events={report['usable_events']}, "
            f"train_events={report['train_events']}, test_events={report['test_events']}, "
            f"json='{args.json_out}', markdown='{args.md_out}')"
        )
        return 0

    if args.command == "scheduled-backtest-report":
        from token_meme_monitor.scheduled_backtest import run_scheduled_backtest_cycle

        result = run_scheduled_backtest_cycle(
            database_path=config.database_path,
            chain_id=config.chain_id,
            json_out=args.json_out,
            md_out=args.md_out,
            archive_dir=args.archive_dir,
            archive=not args.no_archive,
            limit=args.limit,
            train_ratio=args.train_ratio,
            max_price_divergence_pct=args.max_price_divergence_pct,
            top_gainers_limit=args.top_gainers_limit,
            strong_gainer_return_threshold=args.strong_gainer_return_threshold,
            refresh_outcome_limit=args.refresh_outcome_limit,
            refresh_missing_quality=args.refresh_missing_quality,
            skip_refresh_outcomes=args.skip_refresh_outcomes,
        )
        report = result["report"]
        print(
            f"scheduled backtest report completed at {result['ran_at']} "
            f"(rows={report['summary']['total_rows']}, usable_events={report['summary']['usable_events']}, "
            f"top_gainers={report['summary']['top_gainer_count']}, missed={report['summary']['missed_strong_gainer_count']}, "
            f"chase={report['summary']['chase_signal_count']}, refreshed={result['refreshed']}, skipped={result['skipped']}, "
            f"json='{result['json_out']}', markdown='{result['md_out']}', "
            f"archive_json='{result['archive_json']}', archive_markdown='{result['archive_md']}')"
        )
        return 0

    if args.command == "run-scheduled-backtest-worker":
        from token_meme_monitor.scheduled_backtest import run_scheduled_backtest_cycle

        if args.interval_seconds <= 0:
            parser.error("--interval-seconds must be positive")
        while True:
            result = run_scheduled_backtest_cycle(
                database_path=config.database_path,
                chain_id=config.chain_id,
                json_out=args.json_out,
                md_out=args.md_out,
                archive_dir=args.archive_dir,
                archive=not args.no_archive,
                limit=args.limit,
                train_ratio=args.train_ratio,
                max_price_divergence_pct=args.max_price_divergence_pct,
                top_gainers_limit=args.top_gainers_limit,
                strong_gainer_return_threshold=args.strong_gainer_return_threshold,
                refresh_outcome_limit=args.refresh_outcome_limit,
                refresh_missing_quality=args.refresh_missing_quality,
                skip_refresh_outcomes=args.skip_refresh_outcomes,
            )
            report = result["report"]
            print(
                f"scheduled backtest worker cycle completed at {result['ran_at']} "
                f"(rows={report['summary']['total_rows']}, usable_events={report['summary']['usable_events']}, "
                f"top_gainers={report['summary']['top_gainer_count']}, missed={report['summary']['missed_strong_gainer_count']}, "
                f"chase={report['summary']['chase_signal_count']}, refreshed={result['refreshed']}, skipped={result['skipped']}, "
                f"next_interval_seconds={args.interval_seconds})",
                flush=True,
            )
            if args.once:
                return 0
            time.sleep(args.interval_seconds)

    if args.command == "run-worker":
        from token_meme_monitor.orchestrator import MonitorWorker

        worker = MonitorWorker(config)
        try:
            if args.once:
                worker.run_cycle()
            else:
                worker.run_forever()
        finally:
            worker.close()
        return 0

    parser.error("unknown command")
    return 2


def _prediction_dataset_fieldnames(rows: list[dict]) -> list[str]:
    base_fields = [
        "signal_id",
        "pair_address",
        "token_address",
        "token_symbol",
        "token_name",
        "observed_at",
        "score",
        "strategy_version",
        "pair_state",
        "should_alert",
        "predictor_version",
        "prob_2h_up20",
        "prob_6h_up50",
        "prob_24h_up100",
        "risk_6h_dd30",
        "opportunity_score",
        "short_momentum_score",
        "continuation_score",
        "breakout_score",
        "stage",
        "max_return_2h",
        "max_return_6h",
        "max_return_24h",
        "min_return_6h",
        "hit_2h_up20",
        "hit_6h_up50",
        "hit_24h_up100",
        "hit_6h_dd30",
        "sample_count_2h",
        "sample_count_6h",
        "sample_count_24h",
        "outcome_source",
        "base_price_source",
        "base_price_usd",
        "gecko_base_close_usd",
        "price_divergence_pct",
        "quality_flags_json",
        "reasons",
        "risk_flags",
        "prediction_reasons",
    ]
    feature_keys: set[str] = set()
    metadata_keys: set[str] = set()
    for row in rows:
        feature_keys.update((json_loads(row.get("feature_json"), {}) or {}).keys())
        metadata_keys.update((json_loads(row.get("token_metadata_json"), {}) or {}).keys())
    metadata_allowlist = {
        "alpha_score",
        "holder_count",
        "binance_futures_listed",
        "alpha_liquidity",
        "alpha_market_cap",
        "alpha_fdv",
        "alpha_volume_24h",
    }
    return (
        base_fields
        + [f"feature_{key}" for key in sorted(feature_keys)]
        + [f"metadata_{key}" for key in sorted(metadata_keys) if key in metadata_allowlist]
    )


def _prediction_dataset_row(row: dict, fieldnames: list[str]) -> dict:
    output = {field: row.get(field) for field in fieldnames}
    output["reasons"] = ",".join(json_loads(row.get("reasons"), []))
    output["risk_flags"] = ",".join(json_loads(row.get("risk_flags"), []))
    output["prediction_reasons"] = ",".join(json_loads(row.get("prediction_reasons"), []))
    features = json_loads(row.get("feature_json"), {}) or {}
    metadata = json_loads(row.get("token_metadata_json"), {}) or {}
    for key, value in features.items():
        field = f"feature_{key}"
        if field in output:
            output[field] = value
    for key, value in metadata.items():
        field = f"metadata_{key}"
        if field in output:
            output[field] = value
    return output
