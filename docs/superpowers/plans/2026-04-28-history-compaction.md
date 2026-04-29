# History Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe `compact-history` maintenance command that separates recent hot rows from compressed cold payloads.

**Architecture:** SQLite gains hourly snapshot rollups plus compressed archive tables for snapshot raw JSON and signal feature JSON. Repository methods perform dry-run estimation and actual compaction. CLI exposes explicit dry-run/execute modes.

**Tech Stack:** Python stdlib, SQLite, zlib compression, existing `unittest` suite.

---

### Task 1: Repository Schema And Helpers

**Files:**
- Modify: `token_meme_monitor/database.py`
- Test: `tests/test_database.py`

- [x] Add tables `snapshot_hourly_rollups`, `snapshot_raw_archives`, and `signal_feature_archives`.
- [x] Add compression/decompression helpers using `zlib`.
- [x] Add compact feature key filtering for old `signals.feature_json`.

### Task 2: Compaction Behavior

**Files:**
- Modify: `token_meme_monitor/database.py`
- Test: `tests/test_database.py`

- [x] Add `estimate_history_compaction(before)` for dry-run counts and byte estimates.
- [x] Add `compact_history(before, batch_size)` to roll up snapshots and archive large payloads.
- [x] Ensure `list_prediction_dataset_rows()` restores archived full signal features.

### Task 3: CLI

**Files:**
- Modify: `token_meme_monitor/cli.py`
- Test: `tests/test_cli.py`

- [x] Add `compact-history` parser with `--older-than-days`, `--before`, `--dry-run`, `--execute`, `--batch-size`, and `--vacuum`.
- [x] Implement output summary for dry-run and execute.
- [x] Require `--execute` for mutation.

### Task 4: Specs, Docs, Verification

**Files:**
- Add: `openspec/changes/history-compaction/*`
- Modify: `docs/backend-core-logic.md`

- [x] Document the compaction command and data-retention semantics.
- [x] Validate OpenSpec.
- [x] Run focused tests, full test suite, and a local dry-run against `data/monitor.db`.
