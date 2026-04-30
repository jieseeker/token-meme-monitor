from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from token_meme_monitor.data_lifecycle import build_lifecycle_integrity_report
from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.scheduled_backtest import SCHEDULED_BACKTEST_STATE_CACHE_KEY
from token_meme_monitor.utils import parse_datetime, utcnow


def build_health_report(repo: MonitorRepository, *, database_path: str) -> dict[str, Any]:
    report = {
        "database": _database_stats(repo, database_path),
        "pairs": _pair_stats(repo),
        "alpha_seed": _alpha_seed_stats(repo),
        "predictions": _prediction_stats(repo),
        "outcomes": _outcome_stats(repo),
        "risk_enrichment": _risk_enrichment_stats(repo),
        "lifecycle": _lifecycle_stats(repo),
        "scheduled_jobs": _scheduled_job_stats(repo),
    }
    report["severity"] = _severity(report)
    return report


def render_health_report(report: dict[str, Any]) -> str:
    database = report.get("database") or {}
    pairs = report.get("pairs") or {}
    predictions = report.get("predictions") or {}
    outcomes = report.get("outcomes") or {}
    risk = report.get("risk_enrichment") or {}
    lifecycle = report.get("lifecycle") or {}
    alpha_seed = report.get("alpha_seed") or {}
    scheduled = (report.get("scheduled_jobs") or {}).get("scheduled_backtest") or {}
    severity = report.get("severity") or {}
    lines = [
        "Backend Health Report",
        f"- Overall: {severity.get('status', 'unknown')}",
        f"- DB size: {database.get('size_mb')} MB",
        f"- Pairs: total={pairs.get('total')} active={pairs.get('active')} stale_active={pairs.get('stale_active_pairs')} no_snapshot_active={pairs.get('no_snapshot_active_pairs')}",
        f"- Alpha seed: total={alpha_seed.get('total')} seeded={alpha_seed.get('seeded')} seed_failed={alpha_seed.get('seed_failed')}",
        f"- Predictions: total={predictions.get('total')} mature_missing_outcomes={predictions.get('mature_missing_outcomes')}",
        f"- Outcomes: total={outcomes.get('total')} unknown_quality={outcomes.get('unknown_quality_rows')} price_divergence_gt10={outcomes.get('price_divergence_gt10')}",
        f"- Risk enrichment: total={risk.get('total')} high={risk.get('high_risk')} failures={risk.get('failures')}",
        f"- Lifecycle: status={lifecycle.get('status')} findings={lifecycle.get('finding_count')}",
        f"- Scheduled backtest: status={scheduled.get('status')} finished_at={scheduled.get('finished_at')} error={scheduled.get('error') or ''}",
    ]
    return "\n".join(lines)


def _database_stats(repo: MonitorRepository, database_path: str) -> dict[str, Any]:
    path = Path(database_path)
    size_mb = round(path.stat().st_size / 1024 / 1024, 1) if path.exists() else 0.0
    row_counts = {
        table: _scalar(repo, f"SELECT count(*) FROM {table}") or 0
        for table in (
            "tokens",
            "pairs",
            "snapshots",
            "signals",
            "signal_predictions",
            "signal_prediction_outcomes",
            "external_ohlcv",
            "external_trend_metrics",
        )
    }
    largest_objects: list[dict[str, Any]] = []
    try:
        rows = repo._conn.execute(
            """
            SELECT name, sum(pgsize) AS bytes
            FROM dbstat
            GROUP BY name
            ORDER BY bytes DESC
            LIMIT 8
            """
        ).fetchall()
        largest_objects = [{"name": row["name"], "mb": round(float(row["bytes"] or 0) / 1024 / 1024, 1)} for row in rows]
    except Exception:
        largest_objects = []
    return {"path": database_path, "size_mb": size_mb, "row_counts": row_counts, "largest_objects": largest_objects}


def _pair_stats(repo: MonitorRepository) -> dict[str, Any]:
    row = repo._conn.execute(
        """
        SELECT
            count(*) AS total,
            COALESCE(sum(active), 0) AS active,
            count(DISTINCT token_address) AS tokens,
            COALESCE(sum(CASE WHEN active = 1 AND (last_snapshot_at IS NULL OR unixepoch(last_snapshot_at) <= unixepoch('now', '-30 minutes')) THEN 1 ELSE 0 END), 0) AS stale_active_pairs,
            COALESCE(sum(CASE WHEN active = 1 AND last_snapshot_at IS NULL THEN 1 ELSE 0 END), 0) AS no_snapshot_active_pairs
        FROM pairs
        """
    ).fetchone()
    states = [
        dict(item)
        for item in repo._conn.execute(
            """
            SELECT state, active, count(*) AS count
            FROM pairs
            GROUP BY state, active
            ORDER BY count DESC
            """
        ).fetchall()
    ]
    output = dict(row) if row else {}
    output["states"] = states
    return output


def _alpha_seed_stats(repo: MonitorRepository) -> dict[str, Any]:
    row = repo._conn.execute(
        """
        SELECT
            count(*) AS total,
            COALESCE(sum(CASE WHEN json_extract(metadata_json, '$.pair_seeded_at') IS NOT NULL THEN 1 ELSE 0 END), 0) AS seeded,
            COALESCE(sum(CASE WHEN json_extract(metadata_json, '$.pair_seed_failed_at') IS NOT NULL THEN 1 ELSE 0 END), 0) AS seed_failed
        FROM tokens
        WHERE json_extract(metadata_json, '$.is_binance_alpha') IN (1, 'true')
        """
    ).fetchone()
    return dict(row) if row else {"total": 0, "seeded": 0, "seed_failed": 0}


def _prediction_stats(repo: MonitorRepository) -> dict[str, Any]:
    versions = [
        dict(item)
        for item in repo._conn.execute(
            """
            SELECT
                predictor_version,
                count(*) AS count,
                min(short_momentum_score) AS min_short_momentum_score,
                max(short_momentum_score) AS max_short_momentum_score,
                max(continuation_score) AS max_continuation_score,
                max(breakout_score) AS max_breakout_score
            FROM signal_predictions
            GROUP BY predictor_version
            ORDER BY count DESC
            """
        ).fetchall()
    ]
    mature_missing = _scalar(
        repo,
        """
        SELECT count(*)
        FROM signal_predictions pred
        LEFT JOIN signal_prediction_outcomes outcome ON outcome.signal_id = pred.signal_id
        WHERE outcome.signal_id IS NULL
          AND unixepoch(pred.observed_at) <= unixepoch('now', '-25 hours')
        """,
    )
    return {
        "total": _scalar(repo, "SELECT count(*) FROM signal_predictions") or 0,
        "versions": versions,
        "mature_missing_outcomes": mature_missing or 0,
    }


def _outcome_stats(repo: MonitorRepository) -> dict[str, Any]:
    quality = [
        dict(item)
        for item in repo._conn.execute(
            """
            SELECT
                outcome_source,
                base_price_source,
                count(*) AS count,
                COALESCE(sum(CASE WHEN price_divergence_pct IS NOT NULL THEN 1 ELSE 0 END), 0) AS with_price_divergence,
                COALESCE(sum(CASE WHEN abs(price_divergence_pct) > 0.10 THEN 1 ELSE 0 END), 0) AS price_divergence_gt10
            FROM signal_prediction_outcomes
            GROUP BY outcome_source, base_price_source
            ORDER BY count DESC
            """
        ).fetchall()
    ]
    return {
        "total": _scalar(repo, "SELECT count(*) FROM signal_prediction_outcomes") or 0,
        "unknown_quality_rows": _scalar(
            repo,
            """
            SELECT count(*)
            FROM signal_prediction_outcomes
            WHERE outcome_source = 'unknown' OR base_price_source = 'unknown'
            """,
        )
        or 0,
        "price_divergence_gt10": _scalar(
            repo,
            "SELECT count(*) FROM signal_prediction_outcomes WHERE abs(price_divergence_pct) > 0.10",
        )
        or 0,
        "quality": quality,
    }


def _scheduled_job_stats(repo: MonitorRepository) -> dict[str, Any]:
    cached = repo.get_external_json_cache(SCHEDULED_BACKTEST_STATE_CACHE_KEY)
    if cached is None:
        return {
            "scheduled_backtest": {
                "name": "scheduled_backtest",
                "status": "unknown",
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
                "summary": {},
            }
        }
    value = cached.get("value") or {}
    return {
        "scheduled_backtest": {
            "name": value.get("name") or "scheduled_backtest",
            "status": value.get("status") or "unknown",
            "started_at": value.get("started_at"),
            "finished_at": value.get("finished_at"),
            "duration_seconds": value.get("duration_seconds"),
            "summary": value.get("summary") if isinstance(value.get("summary"), dict) else {},
            "error": value.get("error"),
            "recorded_at": cached.get("fetched_at"),
        }
    }


def _risk_enrichment_stats(repo: MonitorRepository) -> dict[str, Any]:
    snapshots = repo.list_latest_risk_snapshots()
    provider_counts: dict[str, int] = {}
    for item in snapshots:
        provider = str(item.get("provider") or "unknown")
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
    return {
        "total": len(snapshots),
        "ok": sum(1 for item in snapshots if item.get("status") == "ok"),
        "failures": sum(1 for item in snapshots if item.get("status") == "failure"),
        "high_risk": sum(1 for item in snapshots if item.get("risk_level") == "high"),
        "unknown_risk": sum(1 for item in snapshots if item.get("risk_level") == "unknown"),
        "providers": provider_counts,
    }


def _lifecycle_stats(repo: MonitorRepository) -> dict[str, Any]:
    report = build_lifecycle_integrity_report(repo)
    return {
        "status": report.get("status"),
        "finding_count": report.get("finding_count", 0),
        "findings": report.get("findings", {}),
    }


def _severity(report: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "database_size": _database_size_severity(report.get("database") or {}),
        "stale_active_pairs": _stale_pair_severity(report.get("pairs") or {}),
        "mature_missing_outcomes": _missing_outcome_severity(report.get("predictions") or {}),
        "lifecycle": _lifecycle_severity(report.get("lifecycle") or {}),
        "scheduled_backtest": _scheduled_backtest_severity(
            ((report.get("scheduled_jobs") or {}).get("scheduled_backtest") or {})
        ),
    }
    overall = "ok"
    for check in checks.values():
        overall = _max_status(overall, str(check.get("status") or "ok"))
    return {"status": overall, "checks": checks}


def _database_size_severity(database: dict[str, Any]) -> dict[str, Any]:
    size_mb = float(database.get("size_mb") or 0.0)
    status = "ok"
    if size_mb >= 2048:
        status = "critical"
    elif size_mb >= 512:
        status = "warn"
    return {"status": status, "size_mb": size_mb, "warn_at_mb": 512, "critical_at_mb": 2048}


def _stale_pair_severity(pairs: dict[str, Any]) -> dict[str, Any]:
    stale = int(pairs.get("stale_active_pairs") or 0)
    active = int(pairs.get("active") or 0)
    status = "ok"
    if stale > 0:
        status = "critical" if active > 0 and stale >= active else "warn"
    return {"status": status, "stale_active_pairs": stale, "active": active}


def _missing_outcome_severity(predictions: dict[str, Any]) -> dict[str, Any]:
    missing = int(predictions.get("mature_missing_outcomes") or 0)
    status = "ok"
    if missing >= 100:
        status = "critical"
    elif missing > 0:
        status = "warn"
    return {"status": status, "mature_missing_outcomes": missing, "critical_at": 100}


def _lifecycle_severity(lifecycle: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": lifecycle.get("status") or "ok",
        "finding_count": lifecycle.get("finding_count", 0),
    }


def _scheduled_backtest_severity(job: dict[str, Any]) -> dict[str, Any]:
    status = str(job.get("status") or "unknown")
    finished_at = parse_datetime(str(job.get("finished_at"))) if job.get("finished_at") else None
    if status == "failure":
        return {"status": "critical", "job_status": status, "finished_at": job.get("finished_at"), "error": job.get("error")}
    if status == "unknown":
        return {"status": "warn", "job_status": status, "finished_at": None}
    if status == "success" and finished_at is not None:
        age = utcnow() - finished_at
        if age >= timedelta(hours=24):
            return {
                "status": "critical",
                "job_status": status,
                "finished_at": job.get("finished_at"),
                "age_seconds": round(age.total_seconds(), 3),
            }
        if age >= timedelta(hours=8):
            return {
                "status": "warn",
                "job_status": status,
                "finished_at": job.get("finished_at"),
                "age_seconds": round(age.total_seconds(), 3),
            }
    return {"status": "ok", "job_status": status, "finished_at": job.get("finished_at")}


def _max_status(left: str, right: str) -> str:
    rank = {"ok": 0, "warn": 1, "critical": 2}
    return left if rank.get(left, 0) >= rank.get(right, 0) else right


def _scalar(repo: MonitorRepository, query: str) -> Any:
    row = repo._conn.execute(query).fetchone()
    if row is None:
        return None
    return row[0]
