## Why

The dashboard can monitor market data and backend state, but the operator still has to mentally connect signals, p4 predictions, later outcomes, scheduled report rows, and backtest results. A decision workbench should make each token case inspectable from first signal through outcome, with filters for wins, misses, stale data, and review notes.

## What Changes

- Add a case-review view that joins signal, prediction, outcome, pair, and report context.
- Add navigation queues for latest high-confidence signals, missed predictions, strong wins, stale data, and report-linked cases.
- Add optional local notes or watchlist state for reviewed cases.
- Add exports for filtered case sets.
- Keep dashboard implementation in Streamlit and reuse backend query helpers.

## Capabilities

### New Capabilities

- `dashboard-decision-workbench`: case review, drill-down, queues, notes, and exports.

### Modified Capabilities

- Existing dashboard pages can link into the decision workbench instead of duplicating case details.

## Impact

- Affects dashboard UI, database read models, optional local notes storage, report links, and documentation.
- Does not replace the backend worker or scheduled report generation.
