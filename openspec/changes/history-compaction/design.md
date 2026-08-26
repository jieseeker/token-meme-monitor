## Context

`snapshots` is the largest table because `raw_json` stores full external payloads. `signals` is the second largest because `feature_json` stores all generated metrics on every refresh. The dashboard primarily reads recent snapshots/signals; model maintenance needs old signal features but does not need them in the hot query table.

## Decisions

- Compaction is CLI-only and never runs automatically in the worker.
- Dry-run is the default mode; mutation requires `--execute`.
- Snapshot cold data is summarized by pair and hour before raw JSON is compacted.
- Signal cold data keeps event-level rows but moves full feature JSON into compressed archives.
- `list_prediction_dataset_rows()` restores archived feature JSON in Python only when the current hot-row `feature_json` still contains the compact placeholder.

## Risks

- `VACUUM` can lock the SQLite database; it is optional and should be run when the dashboard/worker can tolerate a pause.
- Old dashboard history will show compact display features unless a future UI path explicitly expands archived features.
- Compaction is reversible only from the archive tables, not from the main hot columns.
- If a compacted signal row is later repaired and rewritten, prediction dataset reads must not replace the repaired row with the stale archived blob.
