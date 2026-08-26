## ADDED Requirements

### Requirement: Dashboard exposes a case review model
The dashboard SHALL provide a case model that connects the main decision artifacts for a token or signal.

#### Scenario: Case detail is requested
- **WHEN** the user opens a case from a queue or report link
- **THEN** the dashboard shows signal features, pair context, p4 prediction, observed outcome when available, and scheduled report context when available

### Requirement: Review queues are derived from existing workflow states
The dashboard SHALL provide review queues derived from predictions, outcomes, health findings, and report artifacts.

#### Scenario: High-confidence queue is opened
- **WHEN** the user opens the high-confidence queue
- **THEN** the dashboard lists cases sorted by prediction confidence with filters for horizon, score band, age, liquidity, and market cap

#### Scenario: Missed prediction queue is opened
- **WHEN** the user opens the missed-prediction queue
- **THEN** the dashboard lists mature predictions whose outcomes underperformed the prediction expectation

### Requirement: Review notes are isolated from scoring data
The dashboard SHALL allow optional local notes or watchlist state without mutating prediction, outcome, or signal scoring data.

#### Scenario: User saves a note
- **WHEN** the user saves a note for a case
- **THEN** the note is stored in a review-specific location and no prediction, outcome, signal, or score field is changed

### Requirement: Filtered cases can be exported
The dashboard SHALL export the same filtered case set shown to the user.

#### Scenario: Export is requested
- **WHEN** the user exports a filtered queue
- **THEN** the exported rows match the current queue filters and include stable case identifiers
