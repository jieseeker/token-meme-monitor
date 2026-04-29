# History Compaction Design

## Goal

Reduce SQLite pressure from `snapshots` and `signals` without breaking current dashboard detail views, prediction rebuilds, or walk-forward backtests.

## Scope

This change adds an explicit maintenance path. It does not automatically compact data during the worker loop, does not migrate to Postgres, and does not delete prediction or outcome rows.

## Design

Hot data stays in the existing `snapshots` and `signals` tables for the recent retention window. The first implementation defaults to a caller-provided cutoff, usually 14 days before now.

Cold snapshot rows are summarized into a new hourly rollup table, then their large `raw_json` payload is compressed into a side archive table and replaced with `{}` in `snapshots`. The normal trend view still reads recent raw snapshots, while old long-range views can use hourly rollups later.

Cold signal rows keep their event-level identity, score, state, reasons, risk flags, predictions, and outcomes. Their full `feature_json` is compressed into a side archive table, while `signals.feature_json` is replaced with a compact subset that is enough for dashboard history display. Prediction dataset reads restore archived full features in Python so `rebuild-predictions`, `backtest-predictions`, and `export-prediction-dataset` keep the same behavioral input.

The CLI exposes `compact-history` with `--older-than-days`, `--before`, `--dry-run`, `--batch-size`, and `--vacuum`. Dry-run is the default safe inspection path for estimating impact. Actual compaction requires `--execute`.

## Data Boundaries

- `signal_predictions` and `signal_prediction_outcomes` are never compacted.
- `external_ohlcv` remains the durable historical OHLCV source.
- `snapshots.raw_json` is not required for current scoring after the row is written.
- `signals.feature_json` is required for model rebuilds, so it is archived, not discarded.

## Testing

Tests must cover dry-run immutability, snapshot hourly rollup creation, raw JSON archiving, signal feature archiving, prediction dataset feature restoration, and CLI argument behavior.
