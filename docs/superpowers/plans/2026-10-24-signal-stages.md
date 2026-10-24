# Signal stages implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver isolated, replayable A/B/C stage detection, enable safe shadow collection in production, and provide evidence without changing legacy decisions or pretending legacy inputs are verified.

**Architecture:** A deterministic engine consumes one frozen reference pool. Small SQLite state/change tables and existing snapshot metadata support restart, replay and seven-day cleanup. Worker invokes the engine after normal successful writes; a read-only diagnostic API and replay CLI expose results. Legacy list and scheduler remain authoritative during shadow.

**Tech Stack:** Existing Rust, serde, chrono, rusqlite, Axum; no dependencies or new collection providers.

**Spec:** `docs/superpowers/specs/2026-10-24-signal-stages-design.md` and `signal-v2-feasibility-review.md`.

## Global Constraints

- History remains seven days. No production database experiments, destructive migration, long-term history, external notifications, account or order integration.
- Shadow does not write legacy signals or change ranking, existing APIs, or scheduling; mode defaults legacy, deployment explicitly enables shadow.
- Preserve field absence, freeze source, separate data/market risk, never backfill detection times from future peaks.
- Existing DexScreener upstream time is unknown; all shadow hits retain that qualification. OI stays optional and disabled for decisions until verified asset mapping exists.
- User authorizes design choices, development, testing, commit/push, deployment and rollback. Do not pause between routine steps. Native worktree writing was denied; use current `codex/signal-stages` branch.
- Tests precede implementation; keep secrets/config/data/build products out of commits. Record evidence and rulings in `.superpowers/sdd/2026-10-24-signal-stages/progress.md`.

## Review Focus

- Four-minute collection cannot satisfy three non-overlapping m5 samples in ten minutes: freeze only after both price and volume coverage complete (Task 1).
- Duplicate successful responses, out-of-order observations and reference disappearance cannot extend evidence freshness (Tasks 1–2).
- A first-seen hot coin, process restart and seven-day state expiry cannot invent past B/C (Tasks 1–2).
- Failed shadow write must not roll back old snapshots or let later observations bridge missing state (Task 2).
- Replay must process only past inputs, label legacy proxies separately, and never open the source writable or turn forward returns into strategy profit (Task 3).

## Task 1: Deterministic A/B/C engine

**Files:** Create `rust-dashboard/src/engine/signal_v2.rs`; register in `engine/mod.rs`.

**Interfaces:** `Sample {at: i64, pair_address: String, price: Option<f64>, volume_h1: Option<f64>, volume_m5: Option<f64>, liquidity_usd: Option<f64>, fingerprint: String}` derives serde/Clone. `Settings {min_volume_h1_usd: f64, focus_score_threshold: i32}` with defaults matching current rules. Serializable `State` exposes token_address, pair_address, first_seen_at, updated_at, last_evidence_at, stage and data/risk status; internal bounded fields are engine-owned. `State::new(token: &str, sample: &Sample)`, `advance(state: &mut State, sample: &Sample, history: &[Sample], score: Option<i32>, risks: &[String], settings: &Settings)`, `tick(state: &mut State, now: i64)`, `summary(state: &State) -> serde_json::Value`. A `None` score preserves the score entry’s last known predicate; volume entry likewise requires both current h1 and its historical baseline before updating its predicate. Known zero/below-threshold inputs can rearm each independent entry. Runtime passes `Some(score)`; replay passes `None` for missing/malformed/conflicting contemporaneous decisions without suppressing price/volume observations.

- [ ] Write behavioral tests first: hand-built hourly background, first-start sequence, full first-wave/pullback/base/second-start sequence, duplicate/out-of-order/source mismatch, missing/zero distinction, baseline warmup, risk precedence, extended stages, gap/restart/expiry/entry reset and no repeated C. All exact numerical rules come from the design table.
- [ ] Run `cargo test --manifest-path rust-dashboard/Cargo.toml signal_v2` and observe RED from missing behavior.
- [ ] Implement the smallest pure state machine; future history is excluded and all history uses the reference pool. No I/O or OI guesses. Keep base samples bounded. Non-finite/negative inputs cannot confirm.
- [ ] Run targeted tests and the whole Rust suite. Expected: all pass, existing 58 baseline tests remain green.
- [ ] Commit only engine + tests/module registration. Have a fresh reader review correctness and plan compliance; fix important issues with RED→GREEN.

## Task 2: Metadata, persistence, worker and diagnostics

**Files:** Modify `db.rs`, `engine/worker.rs`, `config.rs`, `dashboard.rs`, `.env.example`; add a focused storage module only if needed.

**Interfaces:** Reuse Task 1 types. `SIGNAL_V2_MODE=legacy|shadow`; invalid mode falls back to legacy with a diagnostic. Snapshot `raw_json.signal_v2` contains schema version, field presence, request identity and unverified source time, never credentials or full upstream response. Add independent `signal_v2_state` and `signal_v2_changes` tables with token/time indexes. SQLite helpers load/save State, load same-pool valid Sample history, and list summaries without third-party calls. `GET /api/signal-v2` is read-only and returns mode, unverified-source status and current summaries; it does not alter existing list APIs.

- [ ] Write and run failing tests proving strict metadata excludes old `{}` and missing fields; state roundtrip, change-only summaries, seven-day cutoff, old-query isolation, and successful legacy writes survive a shadow failure.
- [ ] Add additive schema and prune in the current cleanup transaction/budget. Keep old snapshot/signal values unchanged apart from small raw metadata. Shadow processing follows old writes and never modifies old decision or provider scheduling.
- [ ] If shadow processing fails, invalidate its continuous event state before any later advancement or mark the pair's next observation as interrupted. Log structured failure and do not swallow old-path errors. Bound history by one pool and 24 hours with indexed range reads.
- [ ] Add the read-only validated diagnostic route, startup mode log, counters for stage/data/gaps/warmup and controlled shadow errors. No browser score recomputation or new page layout in this task.
- [ ] Run `cargo test`, `node rust-dashboard/static/detail.test.cjs`, `bash deploy-cloud.test.sh`. Expected: all pass. Commit and obtain fresh scoped review.

## Task 3: Honest replay and local acceptance

**Files:** Add `rust-dashboard/src/engine/signal_v2_replay.rs`, register in `engine/mod.rs`, add exact `signal-v2-replay` CLI branch in `main.rs`; document commands in README.

**Interfaces:** `signal-v2-replay [--legacy-proxy]` reads `MONITOR_DATABASE_PATH` via a SQLite READ_ONLY connection and outputs JSON. Strict default accepts new valid metadata only. Proxy mode explicitly labels all results exploratory/unverified and never persists them. Replay calls the same engine, groups simultaneous token observations, freezes pools prospectively, never supplies future samples or today's universe to prior decisions.

- [ ] Write and run failing replay tests on temporary SQLite: future manipulation cannot change past trigger times; metadata-free data excluded strictly; read-only runs leave row counts/schema untouched; zero hits/insufficient samples produce explicit coverage counts; forward horizons without coverage remain unavailable.
- [ ] Implement bounded chronological replay over at most seven days and optional separate legacy-proxy run. Include input/validity/coverage/gap counts, A/B/C event counts and eligible 15m/1h/4h forward sampled-price returns. No profit, fill, slippage or win-rate claim when labels/coverage do not support it.
- [ ] Run strict and proxy replay on a consistent local database copy. Save aggregate results and limitations to `docs/superpowers/reports/2026-10-24-signal-stages-validation.md` without individual address dumps or secrets. Lack of trustworthy historical inputs is a reported finding, not a reason to invent a successful backtest.
- [ ] Build release; start against a temporary database on a separate local port, test old `/api/tokens`, `/api/status`, new diagnostic and legacy/shadow modes. Run complete suites and commit. Obtain scoped review.

## Task 4: Whole-branch review, publish and observe

**Files:** Final validation report, deployment environment only for enabling shadow.

- [ ] Fresh whole-branch review on the strongest available reviewer, explicit no production side effects. Resolve Critical/Important findings with failing regressions and green suite; record all rulings.
- [ ] Verify staged diff contains no secrets/generated files. Fast-forward main only after tests and review, push to the existing origin, verify remote SHA.
- [ ] Use `mac-tencent-alpha` or established identity. Read existing unit paths/config and health first; do not expose env values. Use existing Git-pull deployment path, record prior release for rollback. Enable only shadow mode, retain legacy decisions. No additional rollout approval is needed: the user authorized this step.
- [ ] Verify remote SHA/binary identity, active service, diagnostic mode and old APIs. Observe structured errors/request latency at intervals using the existing 30-minute release window. If service or old APIs regress, restore prior binary/config and restart; keep additive tables for scheduled cleanup.
- [ ] Record observed results, remaining warmup/strategy limitations and exact delivered commit. Return acceptance entry point and the evidence; do not claim long-term effectiveness or true live confirmed signals.
