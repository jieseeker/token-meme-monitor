## Context

The local database now has enough mature prediction outcomes to calibrate p4, but a subset of rows has known quality problems:

- `local_snapshots` outcomes use sparse internal snapshots rather than external hourly candles.
- `price_source_divergence_gt_10pct` indicates the signal price and GeckoTerminal base close disagree too much.
- `partial_*` flags indicate a horizon does not have enough hourly coverage.

The service also needs better operational visibility because `snapshots` and `signals` are the dominant SQLite objects.

## Goals / Non-Goals

**Goals:**

- Keep empirical calibration conservative and label-quality aware.
- Prevent unindexed DexScreener pairs from being retried every normal refresh window.
- Provide one command that summarizes backend data health.
- Keep implementation small and testable.

**Non-Goals:**

- Train a new local ML model.
- Change alert thresholds.
- Replace SQLite or archive old rows in this change.
- Remove the existing low-level CLI maintenance commands.

## Decisions

- Calibration excludes `local_snapshots`, rows over 10 percent price divergence, and rows with the matching partial horizon flag.
  - Rationale: bad labels are worse than sparse labels for probability calibration.

- The divergence threshold is fixed at 10 percent for now.
  - Rationale: it matches existing outcome quality flags and the current backtest filter.

- Unindexed DexScreener retries start at 5 minutes and cap at 1 hour.
  - Rationale: newly created pools may appear shortly after discovery, but permanently missing pools should not starve refresh capacity.

- `health-report` reads existing SQLite tables and does not mutate state.
  - Rationale: it should be safe to run while the worker and dashboard are live.

## Risks / Trade-offs

- Filtering labels reduces calibration sample size from `7504` to `6743`; this is acceptable because it removes known low-quality rows.
- Some unindexed pairs will be noticed later than before; that is preferable to spending every cycle on pairs that external services cannot serve yet.
- Health report totals can move while the worker is running, so counts are operational snapshots rather than transactionally frozen audit numbers.
