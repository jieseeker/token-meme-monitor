# Observation Records Implementation Plan

**Spec:** ../specs/2026-11-05-observation-records.md

- [x] Add failing monitor checks for immutable anchors, same-pool tracking/extrema and grouped repeat events; implement incremental tracking and seven-day report records. Restart, off-list history and retention checks pass.
- [x] Add API archive-on-stale regression and page archive/filter/expiry regression; implement lasting marker, observation-only mode and fixed trigger history. Page checks, 160 Rust tests, research checks and deploy script checks pass; design detector returned no findings.
- [ ] Verify actual rendering and relevant regression gates; commit/push/deploy and check cloud persistence/data freshness.
