## 1. Runtime Status Contract

- [x] 1.1 Add failing tests for PID liveness, stale PID files, missing logs, and command mismatch detection
- [x] 1.2 Implement a runtime status helper with service name, state, PID, command, log path, log size, age, and dashboard URL fields
- [x] 1.3 Expose a CLI command that prints runtime status as text and JSON

## 2. Scheduled Job State

- [x] 2.1 Add failing tests for recording scheduled job success, failure, duration, and last error
- [x] 2.2 Persist latest scheduled report/backtest run state under a stable key
- [x] 2.3 Include scheduled job state in health-report JSON and text renderers

## 3. Restart Workflow

- [x] 3.1 Update `restart.sh status` to consume or mirror the structured runtime status contract
- [x] 3.2 Add log size checks and a safe log rotation or truncation command
- [x] 3.3 Document the supported start, stop, restart, status, and log inspection workflow

## 4. Health Severity

- [x] 4.1 Add failing tests for `ok`, `warn`, and `critical` health classifications
- [x] 4.2 Add severity fields for stale active pairs, missing mature outcomes, scheduled job freshness, and database growth
- [x] 4.3 Preserve backward-compatible health-report counters for existing automation

## 5. Verification

- [x] 5.1 Run focused runtime, CLI, and health tests
- [x] 5.2 Run `bash -n restart.sh`
- [x] 5.3 Run the full unittest suite
