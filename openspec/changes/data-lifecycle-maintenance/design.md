## Context

SQLite is still the right storage layer for this local project, but the database has multiple fast-growing tables and derived artifacts. The recent compaction work introduced archive/placeholder behavior, and the health-report work added visibility. The next maintenance step is to make lifecycle actions predictable: inspect first, plan second, apply only when explicitly requested.

## Goals / Non-Goals

**Goals:**

- Provide a lifecycle inventory that explains database size and age distribution.
- Detect integrity issues that can break backtests, exports, or reports.
- Require dry-run output before cleanup applies retention.
- Protect repairability of compacted history.

**Non-Goals:**

- Move storage away from SQLite.
- Delete historical data automatically.
- Rewrite the compaction model.
- Add cloud backup or remote archival infrastructure.

## Decisions

- Lifecycle commands will default to read-only mode.
  - Rationale: maintenance tooling should be safe to run during investigation.

- Retention plans will be represented as structured JSON before apply.
  - Rationale: tests and operators can compare planned deletions before any mutation occurs.

- Integrity checks will explicitly test the compacted archive invariant.
  - Rationale: archived feature JSON must only replace compact placeholders, not repaired full rows.

- Cleanup apply will require an explicit flag and a local backup/checkpoint path.
  - Rationale: retention mistakes are expensive and difficult to reconstruct.

## Risks / Trade-offs

- Full inventory scans can be slow on large databases. The first pass should keep queries bounded or documented.
- Backup files can consume disk quickly. Retention docs should include cleanup expectations for backups.
- Some integrity findings may be warnings instead of hard failures because the worker can repair them later.
