## 1. Outcome Label Quality

- [x] 1.1 Add failing tests for GeckoTerminal base-price metadata and divergence flags
- [x] 1.2 Add additive SQLite migration for new outcome quality columns
- [x] 1.3 Populate source, base price, divergence, and quality flags during outcome computation
- [x] 1.4 Include outcome quality fields in prediction dataset export
- [x] 1.5 Add an explicit `--refresh-missing-quality` path for backfilling old outcome rows

## 2. Walk-Forward Backtest

- [x] 2.1 Add failing tests for divergence filtering, event de-duplication, and horizon sample denominators
- [x] 2.2 Implement prediction backtest report builder
- [x] 2.3 Add JSON and Markdown report writers
- [x] 2.4 Add `backtest-predictions` CLI command

## 3. Verification

- [x] 3.1 Run focused outcome and backtest tests
- [x] 3.2 Run affected database, CLI, prediction, outcome, and backtest tests
- [x] 3.3 Run the backtest command against the local database
- [x] 3.4 Run the full unittest suite
