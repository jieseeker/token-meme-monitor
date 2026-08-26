## 1. Feedback Data Model

- [x] 1.1 Add failing tests for creating versioned feedback runs and slice rows
- [x] 1.2 Add storage for feedback runs, slice metrics, and recommendation artifacts
- [x] 1.3 Add migration or bootstrap behavior for existing SQLite databases

## 2. Metrics Engine

- [x] 2.1 Add failing tests for win rate, lift, calibration error, precision by score band, and missing-outcome rate
- [x] 2.2 Implement deterministic feedback metrics from existing predictions and outcomes
- [x] 2.3 Enforce minimum sample sizes before emitting slice recommendations

## 3. Recommendation Output

- [x] 3.1 Add failing tests for threshold, ignore-slice, and investigate-slice recommendation formats
- [x] 3.2 Generate review-only strategy candidates with evidence and risk notes
- [x] 3.3 Expose feedback output through CLI text and JSON renderers

## 4. Report and Dashboard Integration

- [x] 4.1 Include the latest compact feedback summary in scheduled reports
- [x] 4.2 Add dashboard read models for feedback run history and slice drill-down
- [x] 4.3 Document how feedback recommendations are reviewed before implementation

## 5. Verification

- [x] 5.1 Run focused feedback, prediction, outcome, and backtest tests
- [x] 5.2 Generate a feedback report against the local database
- [x] 5.3 Run the full unittest suite
