# Scheduled Backtest Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local scheduled backtest report that can run every 4 hours, refresh mature outcomes, summarize prediction calibration, and surface strong-gainer miss/chase problems.

**Architecture:** Keep this out of the realtime monitor worker. Add a focused report module that consumes prediction dataset rows and the existing backtest report, expose a single-cycle CLI, and add a project-local long-running scheduled backtest worker. The worker owns the 4-hour loop inside the project and does not depend on macOS `launchd`, cron, or other system schedulers.

**Tech Stack:** Python standard library, existing SQLite repository, existing prediction backtest module, existing CLI parser.

---

### Task 1: Report Analysis Module

**Files:**
- Create: `token_meme_monitor/scheduled_backtest.py`
- Test: `tests/test_scheduled_backtest.py`

- [ ] **Step 1: Write failing tests**

Create tests that assert the report identifies top gainers, missed strong gainers, chase signals, and calibration warnings from in-memory rows.

- [ ] **Step 2: Run focused tests**

Run: `./.venv/bin/python -m unittest tests.test_scheduled_backtest`

Expected: FAIL because `token_meme_monitor.scheduled_backtest` does not exist.

- [ ] **Step 3: Implement minimal module**

Implement `build_scheduled_backtest_report()`, `render_scheduled_backtest_markdown()`, and `write_scheduled_backtest_outputs()`.

- [ ] **Step 4: Re-run focused tests**

Run: `./.venv/bin/python -m unittest tests.test_scheduled_backtest`

Expected: OK.

### Task 2: CLI Command

**Files:**
- Modify: `token_meme_monitor/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI test**

Add a temp database test that calls `scheduled-backtest-report --json-out ... --md-out ... --skip-refresh-outcomes` and asserts both files are written.

- [ ] **Step 2: Run CLI test**

Run: `./.venv/bin/python -m unittest tests.test_cli.CliTests.test_scheduled_backtest_report_writes_outputs`

Expected: FAIL because the command is unknown.

- [ ] **Step 3: Implement command**

Add parser options for output paths, top gainer limit, minimum return threshold, max price divergence, optional dataset limit, and `--skip-refresh-outcomes`.

- [ ] **Step 4: Re-run CLI test**

Run: `./.venv/bin/python -m unittest tests.test_cli.CliTests.test_scheduled_backtest_report_writes_outputs`

Expected: OK.

### Task 3: Project Worker Command

**Files:**
- Modify: `token_meme_monitor/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing worker CLI test**

Add a temp database test that calls `run-scheduled-backtest-worker --once --json-out ... --md-out ... --skip-refresh-outcomes` and asserts both files are written.

- [ ] **Step 2: Run worker CLI test**

Run: `./.venv/bin/python -m unittest tests.test_cli.CliTests.test_scheduled_backtest_worker_once_writes_outputs`

Expected: FAIL because the command is unknown.

- [ ] **Step 3: Implement worker command**

Add parser options for `--once`, `--interval-seconds`, output paths, archive directory, top gainer limit, return threshold, max price divergence, dataset limit, and outcome refresh controls.

- [ ] **Step 4: Re-run worker CLI test**

Run: `./.venv/bin/python -m unittest tests.test_cli.CliTests.test_scheduled_backtest_worker_once_writes_outputs`

Expected: OK.

### Task 4: Documentation

**Files:**
- Modify: `docs/backend-core-logic.md`

- [ ] **Step 1: Document command and worker setup**

Add exact commands for one-shot report generation and the long-running `run-scheduled-backtest-worker` loop.

- [ ] **Step 2: Verify command help**

Run: `./.venv/bin/python -m token_meme_monitor run-scheduled-backtest-worker --help`

Expected: Help text includes scheduled report options.

### Task 5: Verification

**Files:**
- No new implementation files.

- [ ] **Step 1: Run focused tests**

Run: `./.venv/bin/python -m unittest tests.test_scheduled_backtest tests.test_cli`

Expected: OK.

- [ ] **Step 2: Run full tests**

Run: `./.venv/bin/python -m unittest discover -s tests`

Expected: OK.

- [ ] **Step 3: Generate a real local report once**

Run: `./.venv/bin/python -m token_meme_monitor scheduled-backtest-report --skip-refresh-outcomes --max-price-divergence-pct 0.10`

Expected: Writes `data/backtests/scheduled/latest.json` and `data/backtests/scheduled/latest.md`.

- [ ] **Step 4: Run the worker once**

Run: `./.venv/bin/python -m token_meme_monitor run-scheduled-backtest-worker --once --skip-refresh-outcomes --max-price-divergence-pct 0.10`

Expected: Writes the same latest report and exits without sleeping.
