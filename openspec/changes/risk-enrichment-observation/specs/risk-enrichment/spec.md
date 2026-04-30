## ADDED Requirements

### Requirement: Risk snapshots preserve normalized and source metadata
The system SHALL store token risk snapshots with normalized fields and source metadata.

#### Scenario: Provider returns risk data
- **WHEN** an enabled risk provider returns data for a tracked token
- **THEN** the system stores provider name, fetched time, TTL, confidence, normalized risk fields, and raw source metadata when available

#### Scenario: Provider cannot return risk data
- **WHEN** an enabled risk provider fails, times out, or has no token coverage
- **THEN** the system records the failure reason and retry timing instead of silently treating the token as low risk

### Requirement: Risk enrichment is optional
The system SHALL continue normal market monitoring when risk providers are disabled or unavailable.

#### Scenario: Risk providers are disabled
- **WHEN** the worker refreshes tracked tokens
- **THEN** signal generation and prediction behavior continue without requiring risk snapshots

#### Scenario: Provider call fails
- **WHEN** a risk provider call fails during refresh
- **THEN** the worker records the failure and continues other refresh work

### Requirement: Risk metadata remains observation-only
Risk enrichment SHALL NOT change alert scoring, p4 prediction scoring, or alert suppression in this change.

#### Scenario: High-risk metadata exists
- **WHEN** a token has high-risk observation metadata
- **THEN** reports and dashboard views may display the risk state, but signal scoring and prediction scoring remain unchanged

### Requirement: Reports and dashboard distinguish unknown from low risk
The system SHALL display unknown risk state separately from low-risk state.

#### Scenario: No current risk snapshot exists
- **WHEN** a report or dashboard view renders a tracked token without a current risk snapshot
- **THEN** the output labels risk as unknown rather than low
