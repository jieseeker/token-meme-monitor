# Observation Records Implementation Plan

**Spec:** ../specs/2026-11-05-observation-records.md

- [x] Add failing monitor checks for immutable anchors, same-pool tracking/extrema and grouped repeat events; implement incremental tracking and seven-day report records. Restart, off-list history and retention checks pass.
- [x] Add API archive-on-stale regression and page archive/filter/expiry regression; implement lasting marker, observation-only mode and fixed trigger history. Page checks, 160 Rust tests, research checks and deploy script checks pass; design detector returned no findings.
- [x] Commit/push/deploy and check cloud persistence/data freshness. Cloud runs ea95768; all eight monitoring services active. API has 18 token records / 21 events, all 21 anchors and discovery features unchanged after restart; sampled peaks/drawdowns persist and report time advances. Served HTML matches the tested local file byte-for-byte.
- [ ] Actual browser visual/interaction acceptance: blocked. Native Chrome reload redirects to OKX Wallet extension domain-warning.html; the extension warning page itself has no readable content. No warning bypass or extension/security setting change attempted. Page rendering regressions, archive/filter/search/refresh checks and detector passed, but these do not substitute for an actual rendered-page inspection. User must handle the browser warning before this check can resume.
