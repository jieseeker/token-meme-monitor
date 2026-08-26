## Why

The project now has multiple long-running entry points: the ingestion worker, the scheduled backtest report worker, and the Streamlit dashboard. A root restart script exists, and `health-report` can inspect backend data, but operators still need a durable runtime contract that answers whether each service is actually alive, whether scheduled jobs are succeeding, and whether logs or stale PID files are masking failures.

## What Changes

- Add a structured runtime status layer for local services and expose it from CLI and restart tooling.
- Record the latest scheduled maintenance/report run state so failures are visible without reading logs.
- Extend health reporting with severity for operational checks instead of raw counters only.
- Bound local runtime logs and make stale PID handling explicit.
- Update documentation and tests for the supported local operating workflow.

## Capabilities

### New Capabilities

- `runtime-operability`: local service lifecycle, runtime status, and operator-facing health signals.

### Modified Capabilities

- Existing backend health reporting gains severity and scheduled-job freshness.
- Existing restart tooling becomes the supported wrapper around service lifecycle commands.

## Impact

- Affects CLI commands, restart tooling, scheduled report workers, health-report output, and local operations documentation.
- Does not replace SQLite, Streamlit, launchd, or the existing worker entry points.
