## ADDED Requirements

### Requirement: Strategy feedback runs are versioned
The system SHALL persist each strategy feedback run with enough metadata to compare results over time.

#### Scenario: Feedback run completes
- **WHEN** the feedback analysis finishes
- **THEN** the system records run metadata including time window, generated time, code version when available, prediction count, outcome count, and missing-outcome count

### Requirement: Prediction quality is measured by stable slices
The system SHALL compute prediction quality metrics by stable market and signal slices.

#### Scenario: Slice has enough mature outcomes
- **WHEN** a slice meets the configured minimum sample size
- **THEN** the system records sample count, win rate, lift over baseline, calibration error, precision by score band, and missing-outcome rate

#### Scenario: Slice has too few outcomes
- **WHEN** a slice does not meet the configured minimum sample size
- **THEN** the system records the sample count but does not emit a tuning recommendation for that slice

### Requirement: Feedback generates review-only recommendations
The system SHALL generate strategy recommendations without mutating alert thresholds or scoring weights.

#### Scenario: Weak slice is detected
- **WHEN** a slice underperforms the baseline with enough evidence
- **THEN** the system emits a review-only recommendation with slice key, evidence metrics, suggested action, and risk note

#### Scenario: Strong slice is detected
- **WHEN** a slice outperforms the baseline with enough evidence
- **THEN** the system emits a review-only recommendation with the supporting metrics and no automatic scoring change

### Requirement: Feedback summary is visible in reports
The scheduled report and dashboard SHALL expose the latest strategy feedback summary.

#### Scenario: Latest feedback exists
- **WHEN** a scheduled report or dashboard feedback view is rendered
- **THEN** the output includes the latest run time, key improving slices, key weak slices, and recommendation count
