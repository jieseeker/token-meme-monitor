## 1. Case Read Model

- [x] 1.1 Add failing tests for joining signal, pair, prediction, outcome, and report context into one case model
- [x] 1.2 Implement backend query helpers with pagination and stable sorting
- [x] 1.3 Add JSON-serializable case output for export and dashboard use

## 2. Review Queues

- [x] 2.1 Add failing tests for high-confidence, missed-prediction, strong-win, stale-data, and report-linked queues
- [x] 2.2 Implement queue builders from existing predictions, outcomes, health findings, and report artifacts
- [x] 2.3 Add filters for horizon, score band, token age, liquidity, market cap, and risk state when available

## 3. Dashboard UI

- [x] 3.1 Add the decision workbench page or tab in Streamlit
- [x] 3.2 Render queue table, case detail timeline, prediction/outcome comparison, and report context
- [x] 3.3 Add export controls for the current filtered case set

## 4. Notes and Watchlist

- [x] 4.1 Add failing tests for optional local notes and watchlist persistence
- [x] 4.2 Store review notes separately from prediction and outcome tables
- [x] 4.3 Render notes and watchlist state without changing backend scoring

## 5. Verification

- [x] 5.1 Run focused dashboard query and database tests
- [x] 5.2 Start the dashboard and verify the decision workbench renders locally
- [x] 5.3 Run the full unittest suite
