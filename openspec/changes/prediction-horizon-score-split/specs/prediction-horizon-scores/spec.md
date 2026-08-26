## ADDED Requirements

### Requirement: Prediction result exposes horizon-specific scores
The system SHALL produce separate scores for 2h short momentum, 6h continuation, and 24h breakout observation.

#### Scenario: Prediction is generated
- **WHEN** a signal prediction is built
- **THEN** the result includes `short_momentum_score`, `continuation_score`, and `breakout_score`

#### Scenario: Compatibility score is stored
- **WHEN** a new prediction is stored
- **THEN** `opportunity_score` equals the primary `short_momentum_score`

### Requirement: Dashboard uses short momentum as the primary prediction score
The system SHALL use the short momentum score for candidate gating and sorting before using longer horizon scores.

#### Scenario: Short momentum score is available
- **WHEN** a candidate has `prediction_short_momentum_score`
- **THEN** dashboard filtering and representative-pair selection use it as the prediction score

#### Scenario: Existing row lacks new score columns
- **WHEN** a candidate has no `prediction_short_momentum_score`
- **THEN** dashboard filtering falls back to `prediction_opportunity_score`

### Requirement: Backtest reports horizon scores
The system SHALL summarize prediction backtests with horizon-specific average scores.

#### Scenario: Backtest report is generated
- **WHEN** the user runs `backtest-predictions`
- **THEN** the report includes average 2h, 6h, and 24h scores by bucket
