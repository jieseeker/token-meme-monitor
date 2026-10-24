# V2 Cadence Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure and, only if evidence supports it, correct the mismatch between V2's 300-second evidence cutoff and the observed six-minute BSC sampling cadence.

**Architecture:** Reuse the current V2 engine and read-only replay. A single shared gap constant governs engine continuity and live gap diagnostics. Compare the unchanged baseline binary and a candidate binary against the same consistent database copy; do not change scheduling or provider traffic.

**Tech Stack:** Rust, SQLite, existing Cargo tests and read-only replay CLI.

**Spec:** `docs/superpowers/specs/2026-10-24-signal-cadence-design.md`

## Global Constraints

- A is observation, B is primary, C is second start; no notification or trading execution.
- Keep seven-day retention, legacy queries, collection cadence and third-party request volume unchanged.
- Preserve missing-data fail-closed behavior; source time remains unverified.
- Keep forward endpoint labels on their existing five-minute bracketing rule.
- Do not promote the candidate if same-input comparison worsens quality or lacks sufficient evidence.

## Review Focus

- Exactly 480 seconds is continuous; 481 seconds cancels a pending B and closes C.
- Unconsumed stored snapshots, missing reference pool and invalid input still cancel a pending event even inside 480 seconds.
- Duplicate or unchanged provider content cannot count as another confirmation.
- Version change resets/restores state safely and never affects legacy signal queries.
- Old `>5m` coverage counters remain explicitly five-minute measurements, not a claim of 480-second continuity.

---

### Task 1: Freeze the comparison input and baseline

**Files:** none in application code; output a checksummed temporary SQLite backup and an ignored scratch ledger.

**Interfaces:** Produces one immutable database path and baseline release binary path for Task 3.

- [x] Copy the production DB through SQLite `backup()` from a read-only source into `/tmp`, transfer it to local `/private/tmp`, and record file SHA256, schema digest, snapshot count and UTC range.
- [x] Build the current release binary before edits, run strict and `--legacy-proxy` twice on the copy with focus 65 and h1 minimum $15,000; assert byte-identical same-mode outputs and no source file changes.
- [x] Record baseline A/B/C, risk-PASS B, recent-volume B, label coverage and named-case events in the scratch ledger.

### Task 2: Candidate evidence-gap calibration

**Files:** Modify `rust-dashboard/src/engine/signal_v2.rs`, `rust-dashboard/src/engine/signal_v2_store.rs`, `rust-dashboard/src/dashboard.rs`; tests in existing Rust modules. Experiment version in `signal_v2_store.rs`.

**Interfaces:** Produces candidate release binary using `MAX_EVIDENCE_GAP_SECONDS = 480`; Task 3 consumes it. Existing replay API remains unchanged.

- [x] Write a focused failing engine test asserting a B pending confirmation survives a 360-second new observation but is canceled at 481 seconds, while the 480-second boundary remains live. Run the exact test and see it fail.
- [x] Add one shared constant, use it in `tick` and live gap logging, then update affected old boundary tests; leave B's 900-second/three-sample confirmation and replay label bracketing unchanged. Run focused tests.
- [x] Write and run a failing store regression for gap diagnostics at 480/481 seconds if the engine test does not cover the logging boundary; then make it pass.
- [x] Change both experiment-version fields to `signal_v2_shadow_v2`; update the diagnostic version assertion and ensure version change resets pending state with existing regression coverage.
- [x] Run `cargo fmt --check`, full `cargo test`, and a release build. Keep the candidate uncommitted until Task 3's result.

### Task 3: Compare, reflect and decide

**Files:** Create `docs/superpowers/reports/2026-10-24-signal-cadence-comparison.md`; candidate code from Task 2 is retained only if comparison justifies it.

**Interfaces:** Consumes frozen database and two binaries; produces a versioned go/no-go decision with reproducible counts.

- [x] Run candidate strict and proxy replay twice on the identical copy. Verify deterministic output and unchanged database SHA256/schema/counts.
- [x] Compare all A/B/C, risk and data status, B cohorts, endpoint denominators/missing/censored/positive/negative, and per-token IRYS/FOREST/IN/AKE histories. Record new and lost B events, their risk and observed forward labels.
- [x] Ruling: retain only if added risk-PASS B cases have defensible early timing and quality without an evident regression; otherwise restore the baseline constant and version while keeping the research report. Do not infer market-wide efficacy from four retrospectively chosen coins.
- [x] Review any remaining miss reason; if another independent change cannot be tested with the current strict-history window, stop this cycle and state the evidence boundary instead of tuning again on the same proxy rows.
- [ ] Commit the validated report and, if retained, the tested candidate. Obtain one whole-branch code review and address Critical/Important findings with a failing regression first.

### Task 4: Integrate safely

**Files:** No further feature code.

- [x] Re-run the full relevant test suite and build on the final tree; verify staged diff and sensitive-file exclusions.
- [ ] Merge or fast-forward the reviewed branch to `main` and push, using the user's prior full-delivery authorization. Deploy only if Task 3 produced sufficient evidence to promote the runtime behavior; otherwise publish the report and leave the current shadow binary intact.
- [ ] If deployed, verify legacy and V2 APIs, service health, state reset semantics, and logs; roll back on sustained old-path regression.
