# Token Rule Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align candidate scores with merged token data and add two cache-backed risk observations.

**Architecture:** Aggregate selected pool snapshots in the worker before evaluating one token signal. Derive liquidity withdrawal from prior SQLite snapshots and depth observations from the dashboard's existing cache. Preserve the current API paths and SQLite tables.

**Tech Stack:** Rust, SQLite, embedded HTML/JavaScript, Node assertion checks.

**Spec:** `docs/superpowers/specs/2026-10-23-token-rule-calibration-design.md`

## Global Constraints

- BSC Alpha only; no extra third-party request.
- New risk observations never add or subtract candidate score.
- Local verification only; no cloud deployment.

## Review Focus

- A missing or tiny pool must not inflate the token score or hide the main pool.
- Changing pool membership must not trigger a false liquidity-withdrawal alert.
- Stale or incomplete depth must not produce a risk tag.
- Old stored labels must not reappear during the cache transition.
- List score/reasons and detail risk must describe the same token snapshot.

### Task 1: Token-wide score

**Files:** `rust-dashboard/src/engine/worker.rs`, `rust-dashboard/src/engine/signals.rs`, `rust-dashboard/src/engine/features.rs`

- [x] Test summed liquidity/volume/counts and one shared token score.
- [x] Aggregate selected pools before signal evaluation, remove redundant/context-only bonuses and update liquidity severity; run targeted tests to pass.

### Task 2: Liquidity withdrawal

**Files:** `rust-dashboard/src/db.rs`, `rust-dashboard/src/engine/worker.rs`

- [x] Test the same-pool 15–25 minute comparison and missing-pool case.
- [x] Read prior snapshots once per token and attach the risk tag only for a fall of at least 25%; verify green.

### Task 3: Depth risk and display

**Files:** `rust-dashboard/src/dashboard.rs`, `rust-dashboard/static/index.html`, `rust-dashboard/static/detail.test.cjs`

- [x] Test depth age, 90% liquidity coverage and 1% asymmetry, plus retired-label display.
- [x] Enrich token detail from existing depth cache; use precise labels and keep score unchanged; verify green.

### Task 4: Final verification

- [x] Run `cargo fmt --check`, `cargo test`, `node rust-dashboard/static/detail.test.cjs`, and `git diff --check`.
- [x] Build and restart local service; verify API and visual detail. Review diff for unrelated changes.
