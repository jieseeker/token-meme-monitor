from __future__ import annotations

from pathlib import Path
from typing import Any

from token_meme_monitor.database import MonitorRepository


def build_health_report(repo: MonitorRepository, *, database_path: str) -> dict[str, Any]:
    return {
        "database": _database_stats(repo, database_path),
        "pairs": _pair_stats(repo),
        "alpha_seed": _alpha_seed_stats(repo),
        "predictions": _prediction_stats(repo),
        "outcomes": _outcome_stats(repo),
    }


def render_health_report(report: dict[str, Any]) -> str:
    database = report.get("database") or {}
    pairs = report.get("pairs") or {}
    predictions = report.get("predictions") or {}
    outcomes = report.get("outcomes") or {}
    alpha_seed = report.get("alpha_seed") or {}
    lines = [
        "Backend Health Report",
        f"- DB size: {database.get('size_mb')} MB",
        f"- Pairs: total={pairs.get('total')} active={pairs.get('active')} stale_active={pairs.get('stale_active_pairs')} no_snapshot_active={pairs.get('no_snapshot_active_pairs')}",
        f"- Alpha seed: total={alpha_seed.get('total')} seeded={alpha_seed.get('seeded')} seed_failed={alpha_seed.get('seed_failed')}",
        f"- Predictions: total={predictions.get('total')} mature_missing_outcomes={predictions.get('mature_missing_outcomes')}",
        f"- Outcomes: total={outcomes.get('total')} unknown_quality={outcomes.get('unknown_quality_rows')} price_divergence_gt10={outcomes.get('price_divergence_gt10')}",
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
            COALESCE(sum(CASE WHEN active = 1 AND (last_snapshot_at IS NULL OR last_snapshot_at <= datetime('now', '-30 minutes')) THEN 1 ELSE 0 END), 0) AS stale_active_pairs,
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
          AND pred.observed_at <= datetime('now', '-25 hours')
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


def _scalar(repo: MonitorRepository, query: str) -> Any:
    row = repo._conn.execute(query).fetchone()
    if row is None:
        return None
    return row[0]
