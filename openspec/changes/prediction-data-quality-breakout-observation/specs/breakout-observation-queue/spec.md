## ADDED Requirements

### Requirement: Review-Only Breakout Queue
The system SHALL produce a 24h breakout observation queue that is separate from live alert eligibility and short-momentum opportunity ranking.

#### Scenario: Candidate qualifies for breakout observation
- **WHEN** a prediction has feature evidence associated with 24h breakout potential and is not already a high short-momentum alert
- **THEN** the system includes it in the breakout observation queue with a review-only label

#### Scenario: Queue does not mutate live scoring
- **WHEN** a token appears in the breakout observation queue
- **THEN** the system does not change its signal score, prediction probabilities, pair state, alert eligibility, or Telegram alert behavior

### Requirement: Breakout Candidate Evidence
The system SHALL include evidence and risk context for each breakout observation candidate.

#### Scenario: Candidate evidence is rendered
- **WHEN** a candidate is displayed in reports or dashboard views
- **THEN** the item includes token identity, observed time, pair address, score band, stage, 24h probability or breakout score, volume impulse, quality metadata, risk flags, and overextension reasons when available

#### Scenario: Candidate matures into an outcome
- **WHEN** a breakout observation candidate receives a mature outcome
- **THEN** the report can compare candidate status against realized 2h, 6h, and 24h returns without using that future outcome to select the original candidate
