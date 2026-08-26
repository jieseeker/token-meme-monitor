## Context

Current prediction work focuses on generating p4 probabilities and measuring them with backtests. The useful next step is not another raw prediction output; it is a repeatable feedback layer that says which market conditions are producing useful signals and which ones should be down-weighted or investigated.

## Goals / Non-Goals

**Goals:**

- Persist feedback runs so results can be compared over time.
- Make performance visible by stable, explainable slices.
- Produce candidate changes for review before any scoring mutation.
- Keep calculations deterministic and testable from SQLite data.

**Non-Goals:**

- Train or deploy a new ML model.
- Automatically change alert thresholds or p4 score weights.
- Require external market data beyond data already captured for predictions and outcomes.
- Replace existing walk-forward backtest output.

## Decisions

- Feedback runs will be versioned and timestamped.
  - Rationale: strategy conclusions should be tied to the data window and code version that produced them.

- Slice metrics will include sample count, win rate, precision at top score bands, lift over baseline, calibration error, and missing-outcome rate.
  - Rationale: no single metric is enough to decide whether a slice is reliable.

- Candidate changes will be emitted as recommendations with evidence fields.
  - Rationale: the operator can review candidates before they become scoring or threshold changes.

- The scheduled report will show only the latest compact summary.
  - Rationale: full slice detail belongs in CLI or dashboard drill-down views.

## Risks / Trade-offs

- Over-slicing can create tiny samples and misleading recommendations. The implementation should enforce minimum sample sizes.
- Recommendations can become stale if outcomes are missing. Missing-outcome rate must be part of every feedback run.
- Persisted feedback tables add maintenance surface, but they avoid re-computing expensive analysis for every dashboard view.
