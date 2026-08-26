from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from token_meme_monitor.database import MonitorRepository
from token_meme_monitor.utils import isoformat_utc, json_dumps, utcnow


TIME_COLUMNS = {
    "tokens": "last_seen_at",
    "pairs": "updated_at",
    "snapshots": "observed_at",
    "signals": "observed_at",
    "signal_predictions": "observed_at",
    "signal_prediction_outcomes": "evaluated_at",
    "external_json_cache": "fetched_at",
    "external_ohlcv": "fetched_at",
    "risk_snapshots": "fetched_at",
    "strategy_feedback_runs": "generated_at",
}


def build_lifecycle_inventory(
    repo: MonitorRepository,
    *,
    database_path: str,
    snapshot_retention_days: int = 14,
    cache_retention_days: int = 30,
    risk_retention_days: int = 30,
    strategy_retention_days: int = 90,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = now or utcnow()
    tables = {
        table: _table_inventory(repo, table, time_column)
        for table, time_column in TIME_COLUMNS.items()
        if _table_exists(repo, table)
    }
    path = Path(database_path)
    return {
        "generated_at": isoformat_utc(current_time),
        "database": {
            "path": database_path,
            "size_mb": round(path.stat().st_size / 1024 / 1024, 1) if path.exists() else 0.0,
        },
        "tables": tables,
        "retention_candidates": {
            "snapshots": _count_before(repo, "snapshots", "observed_at", current_time - timedelta(days=snapshot_retention_days)),
            "signals": _count_before(repo, "signals", "observed_at", current_time - timedelta(days=snapshot_retention_days)),
            "external_json_cache": _count_before(repo, "external_json_cache", "fetched_at", current_time - timedelta(days=cache_retention_days)),
            "risk_snapshots": _count_before(repo, "risk_snapshots", "fetched_at", current_time - timedelta(days=risk_retention_days)),
            "strategy_feedback_runs": _count_before(repo, "strategy_feedback_runs", "generated_at", current_time - timedelta(days=strategy_retention_days)),
        },
    }


def build_lifecycle_integrity_report(repo: MonitorRepository, *, now: datetime | None = None) -> dict[str, Any]:
    current_time = now or utcnow()
    findings = {
        "archive_shadowed_by_full_signal": {
            "severity": "warn",
            "count": _scalar(
                repo,
                """
                SELECT count(*)
                FROM signals s
                JOIN signal_feature_archives a ON a.signal_id = s.id
                WHERE json_valid(s.feature_json)
                  AND COALESCE(json_extract(s.feature_json, '$._history_compacted'), 0) != 1
                """,
            ),
            "hint": "Keep signals.feature_json as source of truth when it is no longer a compact placeholder.",
        },
        "orphan_predictions": {
            "severity": "critical",
            "count": _scalar(
                repo,
                """
                SELECT count(*)
                FROM signal_predictions pred
                LEFT JOIN signals s ON s.id = pred.signal_id
                WHERE s.id IS NULL
                """,
            ),
            "hint": "Rebuild or delete prediction rows whose source signal no longer exists.",
        },
        "orphan_prediction_outcomes": {
            "severity": "critical",
            "count": _scalar(
                repo,
                """
                SELECT count(*)
                FROM signal_prediction_outcomes outcome
                LEFT JOIN signal_predictions pred ON pred.signal_id = outcome.signal_id
                WHERE pred.signal_id IS NULL
                """,
            ),
            "hint": "Rebuild outcomes after restoring predictions, or remove orphan outcome rows.",
        },
        "stale_external_json_cache": {
            "severity": "warn",
            "count": _count_before(repo, "external_json_cache", "fetched_at", current_time - timedelta(days=30)),
            "hint": "Review stale cache keys before pruning.",
        },
    }
    finding_count = sum(1 for item in findings.values() if int(item.get("count") or 0) > 0)
    status = "ok"
    for item in findings.values():
        if int(item.get("count") or 0) <= 0:
            continue
        status = _max_status(status, str(item.get("severity") or "ok"))
    return {"generated_at": isoformat_utc(current_time), "status": status, "finding_count": finding_count, "findings": findings}


def build_retention_plan(
    repo: MonitorRepository,
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = now or utcnow()
    cutoff = current_time - timedelta(days=older_than_days)
    return {
        "generated_at": isoformat_utc(current_time),
        "mode": "dry-run",
        "cutoff": isoformat_utc(cutoff),
        "requires_explicit_apply": True,
        "actions": {
            "compact_snapshots": {
                "candidate_rows": _count_before(repo, "snapshots", "observed_at", cutoff),
                "mutation": "compact-history",
            },
            "compact_signals": {
                "candidate_rows": _count_before(repo, "signals", "observed_at", cutoff),
                "mutation": "compact-history",
            },
            "prune_external_json_cache": {
                "candidate_rows": _count_before(repo, "external_json_cache", "fetched_at", cutoff),
                "mutation": "delete-cache-rows",
            },
            "prune_expired_risk_snapshots": {
                "candidate_rows": _count_before(repo, "risk_snapshots", "fetched_at", cutoff),
                "mutation": "delete-risk-snapshots",
            },
        },
    }


def render_lifecycle_inventory(report: dict[str, Any]) -> str:
    lines = ["Data Lifecycle Inventory", f"- DB size: {(report.get('database') or {}).get('size_mb')} MB"]
    for table, stats in (report.get("tables") or {}).items():
        lines.append(f"- {table}: rows={stats.get('rows')} oldest={stats.get('oldest')} newest={stats.get('newest')}")
    return "\n".join(lines)


def render_lifecycle_integrity(report: dict[str, Any]) -> str:
    lines = ["Data Lifecycle Integrity", f"- Status: {report.get('status')}", f"- Findings: {report.get('finding_count')}"]
    for name, finding in (report.get("findings") or {}).items():
        lines.append(f"- {name}: severity={finding.get('severity')} count={finding.get('count')}")
    return "\n".join(lines)


def render_retention_plan(plan: dict[str, Any]) -> str:
    lines = ["Retention Plan", f"- Mode: {plan.get('mode')}", f"- Cutoff: {plan.get('cutoff')}"]
    for name, action in (plan.get("actions") or {}).items():
        lines.append(f"- {name}: candidate_rows={action.get('candidate_rows')} mutation={action.get('mutation')}")
    return "\n".join(lines)


def _table_inventory(repo: MonitorRepository, table: str, time_column: str) -> dict[str, Any]:
    row = repo._conn.execute(
        f"""
        SELECT count(*) AS rows, min({time_column}) AS oldest, max({time_column}) AS newest
        FROM {table}
        """
    ).fetchone()
    return {"rows": int(row["rows"] or 0), "oldest": row["oldest"], "newest": row["newest"]}


def _table_exists(repo: MonitorRepository, table: str) -> bool:
    row = repo._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _count_before(repo: MonitorRepository, table: str, column: str, cutoff: datetime) -> int:
    if not _table_exists(repo, table):
        return 0
    row = repo._conn.execute(
        f"SELECT count(*) FROM {table} WHERE {column} < ?",
        (isoformat_utc(cutoff),),
    ).fetchone()
    return int(row[0] or 0)


def _scalar(repo: MonitorRepository, query: str) -> int:
    row = repo._conn.execute(query).fetchone()
    return int(row[0] or 0) if row else 0


def _max_status(left: str, right: str) -> str:
    rank = {"ok": 0, "warn": 1, "critical": 2}
    return left if rank.get(left, 0) >= rank.get(right, 0) else right


def to_json_text(report: dict[str, Any]) -> str:
    return json_dumps(report)
