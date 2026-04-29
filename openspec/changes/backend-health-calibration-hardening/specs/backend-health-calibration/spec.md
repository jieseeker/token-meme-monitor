## ADDED Requirements

### Requirement: Prediction calibration uses only quality-eligible outcomes
The system SHALL ignore low-quality prediction outcomes when building empirical calibration buckets.

#### Scenario: Outcome was produced from local snapshots
- **WHEN** a mature prediction outcome has `outcome_source` equal to `local_snapshots`
- **THEN** it is not used as empirical calibration evidence

#### Scenario: Outcome price source diverges materially
- **WHEN** a mature prediction outcome has `price_divergence_pct` greater than 10 percent in absolute value
- **THEN** it is not used as empirical calibration evidence

#### Scenario: Horizon has partial coverage
- **WHEN** a mature prediction outcome has the matching partial horizon quality flag
- **THEN** that horizon is not counted as an eligible calibration label

### Requirement: Unindexed DexScreener pairs use longer retry backoff
The worker SHALL schedule a longer retry window when DexScreener cannot return a pair snapshot yet.

#### Scenario: Pair snapshot is not available
- **WHEN** DexScreener returns no snapshot for a tracked pair
- **THEN** the pair retry time is delayed by the unindexed-pair backoff and retry metadata is persisted

### Requirement: Backend health can be inspected from CLI
The system SHALL provide a read-only health report command for backend maintenance.

#### Scenario: Health report is requested as JSON
- **WHEN** the user runs `health-report --json`
- **THEN** the command prints database size, row counts, pair freshness, Alpha seed status, prediction status, and outcome quality counts
