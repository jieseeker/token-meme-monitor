## ADDED Requirements

### Requirement: Lifecycle inventory reports database growth drivers
The system SHALL provide a read-only lifecycle inventory for database maintenance.

#### Scenario: Inventory is requested
- **WHEN** the user runs lifecycle inventory in text or JSON mode
- **THEN** the output includes table row counts, approximate size signals when available, oldest and newest timestamps, and retention candidate counts

### Requirement: Integrity checks protect derived history
The system SHALL detect integrity issues that can make backtests, exports, or reports unreliable.

#### Scenario: Compacted signal has repaired full features
- **WHEN** a signal row has full repaired feature JSON and also has an archived compacted payload
- **THEN** lifecycle integrity reports that the full row must take precedence over the archive

#### Scenario: Derived row has no source row
- **WHEN** a prediction or outcome references a missing signal or pair
- **THEN** lifecycle integrity reports an orphan finding with severity and remediation hint

### Requirement: Retention cleanup is previewed before apply
The system SHALL produce a structured dry-run plan before any destructive retention action.

#### Scenario: Retention dry-run is requested
- **WHEN** the user runs retention planning without apply
- **THEN** the system returns planned deletions or compactions without mutating the database

#### Scenario: Retention apply is requested without backup or explicit confirmation
- **WHEN** the user tries to apply a destructive retention plan without required safety flags
- **THEN** the command refuses to mutate the database

### Requirement: Lifecycle health integrates with health-report
Health-report SHALL include a compact lifecycle summary.

#### Scenario: Health report is requested
- **WHEN** lifecycle checks have findings
- **THEN** health-report includes lifecycle severity and finding counts while preserving existing counters
