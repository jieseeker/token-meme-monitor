## Why

The project now stores snapshots, signals, compacted feature archives, predictions, outcomes, caches, and scheduled report artifacts in SQLite. History compaction reduces row weight, but the operator still needs dry-run retention plans, integrity checks, and safe cleanup workflows before database growth becomes a reliability problem.

## What Changes

- Add lifecycle inventory reporting for table sizes, row counts, age ranges, and retention candidates.
- Add integrity checks for compacted signal archives, orphaned predictions/outcomes, stale cache rows, and missing outcome backlogs.
- Add dry-run retention planning before destructive cleanup.
- Add backup/checkpoint guidance or automation before applying cleanup.
- Coordinate lifecycle reporting with health-report and documentation.

## Capabilities

### New Capabilities

- `data-lifecycle`: database inventory, retention planning, integrity checks, and safe cleanup workflow.

### Modified Capabilities

- Existing history compaction and health reporting gain lifecycle-aware diagnostics.

## Impact

- Affects CLI maintenance commands, database helpers, health-report output, compaction checks, and documentation.
- Destructive cleanup remains opt-in and must be previewed before apply.
