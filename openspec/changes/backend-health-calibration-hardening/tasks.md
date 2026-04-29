## 1. Calibration Quality Gates

- [x] 1.1 Add failing tests for excluding low-quality outcomes from empirical calibration
- [x] 1.2 Exclude local snapshot outcomes from calibration
- [x] 1.3 Exclude price-source divergence above 10 percent
- [x] 1.4 Exclude horizon labels with partial coverage flags

## 2. DexScreener Retry Backoff

- [x] 2.1 Add failing test for unindexed DexScreener snapshot retry metadata
- [x] 2.2 Persist not-yet-indexed retry count and backoff metadata
- [x] 2.3 Schedule longer retry windows for missing DexScreener snapshots

## 3. Health Report

- [x] 3.1 Add failing CLI test for backend health counts
- [x] 3.2 Add health report builder and text/JSON renderers
- [x] 3.3 Expose `health-report` CLI command

## 4. Verification

- [x] 4.1 Run focused prediction/orchestrator/CLI tests
- [x] 4.2 Run affected database, backtest, outcome, and prediction tests
- [x] 4.3 Rebuild local predictions with quality-gated calibration
- [x] 4.4 Run walk-forward backtest against the local database
- [x] 4.5 Run full unittest suite
