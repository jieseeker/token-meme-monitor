## 1. Lifecycle Inventory

- [x] 1.1 Add failing tests for table row counts, approximate size reporting, age ranges, and retention candidate counts
- [x] 1.2 Implement lifecycle inventory builder and text/JSON renderers
- [x] 1.3 Expose lifecycle inventory from CLI

## 2. Integrity Checks

- [x] 2.1 Add failing tests for compacted archive invariants, orphan predictions, orphan outcomes, stale cache rows, and missing mature outcomes
- [x] 2.2 Implement read-only lifecycle integrity checks with severity and remediation hints
- [x] 2.3 Include lifecycle integrity summary in health-report

## 3. Retention Planning

- [x] 3.1 Add failing tests for dry-run retention plans and no-op behavior
- [x] 3.2 Implement structured retention plans for snapshots, caches, reports, and old compacted artifacts
- [x] 3.3 Require explicit apply and backup/checkpoint settings before destructive cleanup

## 4. Documentation

- [x] 4.1 Document safe lifecycle workflow: inventory, integrity, dry-run, backup, apply, verify
- [x] 4.2 Document retention defaults and how they interact with history compaction
- [x] 4.3 Add examples for JSON output suitable for scheduled checks

## 5. Verification

- [x] 5.1 Run focused database, health, compaction, and CLI tests
- [x] 5.2 Run lifecycle inventory and dry-run commands against the local database
- [x] 5.3 Run the full unittest suite
