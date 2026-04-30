## Context

The current runtime is intentionally simple: Python CLIs run the backend worker and scheduled report worker, Streamlit serves the dashboard, and `restart.sh` manages local PID and log files under `/tmp/token-meme-monitor`. That is enough for local operation, but the monitoring surface is split across PID files, logs, `health-report`, and dashboard behavior.

## Goals / Non-Goals

**Goals:**

- Provide one machine-readable status contract for all local services.
- Make scheduled job success and failure visible through normal health reporting.
- Keep the local restart workflow shell-friendly and dependency-light.
- Preserve read-only health checks unless a command explicitly starts, stops, or rotates runtime files.

**Non-Goals:**

- Replace local scripts with Docker, systemd, launchd, or a hosted supervisor.
- Introduce remote observability infrastructure.
- Change alert scoring, prediction scoring, or data retention policy.
- Require external network access for runtime status.

## Decisions

- Runtime status will treat PID liveness, process command matching, log path, log size, and dashboard URL as separate fields.
  - Rationale: a PID can exist while the wrong process is running, and a process can be healthy while the dashboard URL is the only user-facing endpoint.

- Scheduled job state will be persisted through existing lightweight storage first, preferably a namespaced `external_json_cache` key.
  - Rationale: it avoids a migration for the first pass while still giving health-report and dashboard code a stable read model.

- Health-report will keep existing counters and add a derived severity for each operational check.
  - Rationale: raw counts are useful for debugging, but operators need a quick `ok`, `warn`, or `critical` summary.

- `restart.sh` remains a convenience wrapper, not the source of truth for business logic.
  - Rationale: lifecycle orchestration should stay small, while data freshness and service-state logic belongs in tested Python helpers.

## Risks / Trade-offs

- Process inspection is OS-sensitive. The first pass will target the current macOS/local shell workflow and keep parsing isolated for tests.
- Persisting scheduler state in the JSON cache is less explicit than a dedicated table, but it avoids schema churn until the run-state model proves stable.
- Severity thresholds can become noisy if set too aggressively; thresholds should start conservative and be documented.
