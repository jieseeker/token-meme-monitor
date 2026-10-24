# Signal quality comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Compare fixed B quality filters using honest sampled outcomes without changing runtime rules.

**Architecture:** Extend the existing read-only streaming replay. Freeze cohort membership at each B event and share its pending forward labels across aggregate/date cohorts. Keep original A/B/C totals intact.

**Tech Stack:** Rust, rusqlite, serde_json, chrono; existing dependencies only.

**Spec:** `docs/superpowers/specs/2026-10-24-signal-quality-design.md`

## Global Constraints

- Seven-day maximum, no new collectors/tables/notifications/dependencies or runtime engine/scheduler changes.
- Positive sampled direction is not trading win rate; no provenance, missingness or execution-parity claims.
- Source is READ_ONLY; no per-address reports, no secrets, no generated files in Git.
- User delegated routine design/testing/commit/push/cloud delivery. Single implementer, fresh final review.

## Review Focus

- Same-second conflicting crossing samples must yield unknown, never select a favorable volume.
- Future price/volume/liquidity changes cannot reclassify an existing event's filters.
- Risk-BLOCK and missing volume must not inflate the PASS baseline or filtered rates.
- Missing and right-censored endpoints must not enter the direction-rate denominator.
- New comparison paths must preserve existing strict/proxy decoding, original triggers and read-only source integrity.

### Task 1: Fixed cohorts and sampled outcome accounting

**Files:** Modify/test `rust-dashboard/src/engine/signal_v2_replay.rs`; document `README.md`.

**Interfaces:** Keep `run(path,args,settings) -> Result<Value>` unchanged. Add direction counters/rate to `Outcome`, immutable per-event cohort keys to `Pending`, `b_filter_comparison` to JSON. Existing `structural_hits` contains exactly A/B/C. Record aggregate and UTC-date cohorts using event-time evidence only.

- [ ] Write failing behavior tests through real SQLite replay: positive/negative/flat versus missing/censored denominator; original B remains one event; risk/missing volume gate; recent-volume positive and negative cases; crossed-volume conflicts unknown; 3/5/8% bucket boundaries; future suffix cannot change cohort classification; date groups use event UTC day and empty replay returns explicit zero cohorts.
- [ ] Run `cargo test --locked --manifest-path rust-dashboard/Cargo.toml signal_v2_replay`; observe new assertions fail for missing behavior.
- [ ] Implement cohort matching at trigger, share label accounting, expose methods/limits and update README. Use the spec's fixed 80%, 1/12 and3% values; do not search parameters.
- [ ] Run focused tests, full Rust tests, existing Node detail and deployment-shell checks. Expected all pass; no new warnings or dependencies.
- [ ] Commit only replay/tests/README and design/plan as appropriate.

### Task 2: Real-data validation, review and delivery

**Files:** Create `docs/superpowers/reports/2026-10-24-signal-quality-validation.md`; deployment uses existing tools.

**Interfaces:** Task1's JSON contains original stage counts plus `b_filter_comparison` and direction outcomes. Base release from `0811b22` provides independent original counts. Copies stay in this plan's ignored scratch.

- [ ] Build locked release and run strict/proxy twice on consistent local and cloud copies; preserve source hashes/schema/counts, verify deterministic JSON and original triggers versus base. Record exact dataset range, cohort counts, endpoint missingness and chronological results. Expected reproducible results; insufficiency is a finding, not an invented successful strategy test.
- [ ] Fresh whole-branch review with plan/spec and Review Focus. Fix Important/Critical findings in one RED→GREEN pass and rerun full suite; ledger deferred minors and rulings.
- [ ] Verify clean diff/no secrets/build/DB files, fast-forward main, push and verify remote SHA. Existing full delivery authorization applies.
- [ ] Deploy exact source through existing Git-pull path without synchronizing unrelated local credentials/proxy settings. Retain rollback binary, verify old APIs/active service/shadow mode and source/binary identity; observe short post-release health because runtime logic is unchanged.
- [ ] Record result and limitations, retain original runtime thresholds unless valid evidence supports promotion. Commit/push final report and clean only this plan's scratch/copies.
