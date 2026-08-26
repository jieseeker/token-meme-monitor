## ADDED Requirements

### Requirement: Cold snapshot rows are rollup-ready before payload compaction
The system SHALL create hourly snapshot rollups for rows older than the compaction cutoff.

#### Scenario: Snapshot history is compacted
- **WHEN** the user executes history compaction with a cutoff
- **THEN** old snapshot rows are summarized into hourly rollups keyed by pair and hour

### Requirement: Cold payloads are compressed outside hot columns
The system SHALL move large cold JSON payloads into compressed archive tables before replacing hot-table payload columns.

#### Scenario: Snapshot raw JSON is compacted
- **WHEN** a snapshot row older than the cutoff has non-empty `raw_json`
- **THEN** the original payload is stored in `snapshot_raw_archives` and `snapshots.raw_json` becomes `{}`

#### Scenario: Signal feature JSON is compacted
- **WHEN** a signal row older than the cutoff has full `feature_json`
- **THEN** the original payload is stored in `signal_feature_archives` and `signals.feature_json` becomes a compact display subset

### Requirement: Prediction maintenance can read still-compacted full features
The system SHALL restore archived full signal features when loading prediction dataset rows only while the signal hot row still contains the compact placeholder.

#### Scenario: Prediction dataset includes still-compacted signal
- **WHEN** a signal has archived full features and its current `feature_json` contains `_history_compacted:true`
- **THEN** `list_prediction_dataset_rows()` returns the archived full `feature_json`

#### Scenario: Prediction dataset includes repaired compacted-history signal
- **WHEN** a signal has archived full features but its current `feature_json` no longer contains `_history_compacted:true`
- **THEN** `list_prediction_dataset_rows()` returns the current hot-row `feature_json`
- **AND** the stale archived blob does not overwrite the repair

### Requirement: History compaction is explicit and inspectable
The system SHALL provide a CLI command that estimates compaction impact without mutation by default.

#### Scenario: User runs dry-run
- **WHEN** the user runs `compact-history --dry-run`
- **THEN** the command prints eligible snapshot and signal counts without mutating rows

#### Scenario: User runs execute
- **WHEN** the user runs `compact-history --execute`
- **THEN** the command performs compaction and prints changed row counts
