## ADDED Requirements

### Requirement: Local services expose structured runtime status
The system SHALL expose a machine-readable runtime status for each supported local service.

#### Scenario: Service process is running
- **WHEN** a supported service PID exists and the PID belongs to the expected command
- **THEN** runtime status reports the service as running with PID, command, log path, log size, and service-specific endpoint fields

#### Scenario: PID file is stale
- **WHEN** a PID file exists but no matching process is alive
- **THEN** runtime status reports the service as stopped with a stale PID diagnostic

#### Scenario: Process command does not match the service
- **WHEN** a PID belongs to a live process that is not the expected service command
- **THEN** runtime status reports a command mismatch instead of marking the service healthy

### Requirement: Scheduled jobs publish latest run state
The system SHALL persist the latest scheduled maintenance or report run state in a form health-report can read.

#### Scenario: Scheduled run succeeds
- **WHEN** a scheduled report or backtest cycle finishes successfully
- **THEN** the system records the run name, status, started time, finished time, duration, and summary counts

#### Scenario: Scheduled run fails
- **WHEN** a scheduled report or backtest cycle fails
- **THEN** the system records the failure status and last error without preventing the next scheduled cycle

### Requirement: Health report includes operational severity
The system SHALL classify operational health checks as `ok`, `warn`, or `critical` while retaining raw counters.

#### Scenario: Health report is requested
- **WHEN** the user runs health-report in text or JSON mode
- **THEN** the output includes severity for service freshness, scheduled job freshness, missing mature outcomes, stale active pairs, and database growth

### Requirement: Restart workflow manages local runtime files predictably
The root restart workflow SHALL handle stale PID files and bounded logs in a predictable way.

#### Scenario: Restart status is requested
- **WHEN** the user runs the restart status command
- **THEN** the command reports each supported service state without starting or stopping services

#### Scenario: Runtime logs exceed the configured size
- **WHEN** a supported service log exceeds the configured local size limit
- **THEN** the restart workflow can rotate or truncate that log without deleting unrelated files
