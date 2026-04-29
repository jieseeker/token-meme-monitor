## Why

The local SQLite database is now around 2.28GB. The largest objects are `snapshots` and `signals`, mostly because every refresh stores raw DexScreener JSON and full feature JSON. Recent detail views need full fidelity, but old rows mainly need summary, prediction, and outcome context.

## What Changes

- Add hourly rollups for cold snapshot rows.
- Compress old `snapshots.raw_json` into a side archive table and replace the hot-table payload with `{}`.
- Compress old `signals.feature_json` into a side archive table and replace the hot-table payload with a compact display subset.
- Restore archived full signal features when building prediction datasets, rebuilding predictions, or backtesting.
- Add a `compact-history` CLI command with dry-run by default and explicit `--execute` for mutation.

## Impact

- No worker behavior change.
- No prediction/outcome table compaction.
- Existing dashboards keep recent full-detail behavior.
- Old history keeps signal summaries and can still support model rebuilds through archived features.
