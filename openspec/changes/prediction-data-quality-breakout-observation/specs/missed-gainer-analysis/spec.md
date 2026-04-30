## ADDED Requirements

### Requirement: Missed Strong Gainer Analysis
The system SHALL analyze realized strong gainers and explain why each was not elevated by the signal or prediction surfaces.

#### Scenario: Strong gainer missed by short-momentum score
- **WHEN** a token reaches the configured strong-gainer return threshold and its short-momentum score is below the review threshold
- **THEN** the analysis includes a miss reason identifying the low short-momentum score

#### Scenario: Strong gainer missed by signal state
- **WHEN** a token reaches the configured strong-gainer return threshold but did not enter focused or alerted state
- **THEN** the analysis includes a miss reason identifying the non-priority signal state

### Requirement: Missed Gainer Grouping
The system SHALL summarize missed strong gainers by stable dimensions that can be compared across scheduled runs.

#### Scenario: Grouping by prediction and signal dimensions
- **WHEN** a scheduled report is generated
- **THEN** missed strong gainers are grouped by score band, prediction stage, signal state, and miss reason

#### Scenario: Grouping preserves case review context
- **WHEN** a missed-gainer group is shown
- **THEN** representative cases include token identity, pair address, observed time, realized returns, prediction scores, and prediction reasons
