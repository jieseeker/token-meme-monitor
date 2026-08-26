## Why

The prediction pipeline now has enough stored rows to evaluate calibration, but the usable event set and high-score buckets are still sparse after quality filtering. Recent scheduled reports also show strong 24h gainers being classified as low short-momentum opportunities, so the project needs better observation surfaces before changing live scoring weights.

## What Changes

- Add a data-quality backlog view for mature predictions that still lack usable outcomes or rely on lower-quality local snapshot labels.
- Add missed-gainer analysis that groups strong 24h winners by miss reason, signal state, score band, stage, and available feature context.
- Add a 24h breakout observation queue that identifies candidates for review without changing alert eligibility or p4 probability calibration.
- Add scheduled-report and dashboard surfaces for the new observation outputs.
- Keep all recommendations review-only; no automatic threshold, score, alert, or trading-decision changes.

## Capabilities

### New Capabilities

- `prediction-data-quality-backlog`: Tracks outcome coverage, quality gaps, and stale prediction rows that should be refreshed or excluded from calibration.
- `missed-gainer-analysis`: Explains why strong realized gainers were not elevated by the signal or prediction surfaces.
- `breakout-observation-queue`: Produces a review-only 24h breakout candidate list separate from short-momentum alerting.

### Modified Capabilities

- None.

## Impact

- Affects prediction dataset reporting, scheduled backtest summaries, dashboard decision workbench data, and documentation.
- May add repository queries, view models, CLI/report output fields, and focused tests around report generation.
- Does not change live signal scoring, alert thresholds, Telegram alerts, p4 probability formulas, or empirical calibration gates in this change.
