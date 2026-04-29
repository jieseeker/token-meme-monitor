## 1. Storage

- [x] 1.1 Add failing tests for dry-run, rollup, archiving, and feature restoration
- [x] 1.2 Add snapshot hourly rollup table
- [x] 1.3 Add compressed snapshot raw JSON archive table
- [x] 1.4 Add compressed signal feature JSON archive table

## 2. Repository Behavior

- [x] 2.1 Add dry-run estimate method
- [x] 2.2 Add actual compaction method
- [x] 2.3 Restore archived full features for prediction dataset reads

## 3. CLI

- [x] 3.1 Add `compact-history` parser
- [x] 3.2 Make dry-run the default
- [x] 3.3 Require `--execute` for mutation
- [x] 3.4 Add optional `--vacuum`

## 4. Verification

- [x] 4.1 Run focused database and CLI tests
- [x] 4.2 Run affected backend tests
- [x] 4.3 Run full unittest suite
- [x] 4.4 Run local dry-run against `data/monitor.db`
