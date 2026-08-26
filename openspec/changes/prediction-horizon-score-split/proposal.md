## Why

The previous single `opportunity_score` mixed 2h, 6h, and 24h objectives even though current walk-forward results support short-horizon momentum more clearly than 24h continuation. Splitting the score prevents the dashboard from presenting sparse 24h evidence as a strong unified signal.

## What Changes

- Add three prediction scores:
  - `short_momentum_score`: primary 2h sorting score.
  - `continuation_score`: 6h continuation score.
  - `breakout_score`: 24h breakout observation score.
- Keep legacy `opportunity_score` as a compatibility alias for primary short-momentum sorting.
- Store the new scores in `signal_predictions` with additive SQLite migration.
- Expose the new scores in dashboard queries, dataset export, and walk-forward reports.
- Update dashboard labels to distinguish short-term opportunity from continuation/breakout observation.

## Capabilities

### New Capabilities

- `prediction-horizon-scores`: Prediction results provide separate 2h, 6h, and 24h score surfaces.

### Modified Capabilities

- None.

## Impact

- Affects prediction model output, `signal_predictions` schema, dashboard sorting/display, CSV export, and backtest reports.
- Existing rows remain readable; new score columns are nullable and fall back to `opportunity_score` where needed.
