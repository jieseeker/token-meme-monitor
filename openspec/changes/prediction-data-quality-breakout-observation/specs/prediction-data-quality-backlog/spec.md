## ADDED Requirements

### Requirement: Outcome Quality Backlog
The system SHALL provide a prediction data-quality backlog that separates mature missing outcomes, local snapshot outcomes, price-divergent external outcomes, and partial horizon coverage.

#### Scenario: Mature predictions missing outcomes are counted
- **WHEN** a prediction is older than the configured outcome maturity window and has no outcome row
- **THEN** the backlog reports it in the mature missing outcome count

#### Scenario: Lower-quality local labels are counted separately
- **WHEN** an outcome was produced from local snapshots rather than GeckoTerminal hourly OHLCV
- **THEN** the backlog reports it separately from high-quality external outcome labels

#### Scenario: Price-divergent external labels remain visible
- **WHEN** an external outcome has absolute price divergence above the configured quality threshold
- **THEN** the backlog reports it as price-divergent and excludes it from high-quality calibration-ready counts

### Requirement: Backlog Refresh Guidance
The system SHALL report how many backlog rows are eligible for refresh and how many rows were skipped in the latest refresh attempt.

#### Scenario: Refresh attempt succeeds partially
- **WHEN** a refresh command updates some mature prediction outcomes and skips others
- **THEN** the backlog records updated and skipped counts so the operator can distinguish completed coverage from external data gaps

#### Scenario: Scheduled refresh limit is insufficient
- **WHEN** mature missing outcome count remains above the configured critical threshold after a successful scheduled run
- **THEN** the backlog indicates that refresh throughput is insufficient for the current prediction volume

### Requirement: Stale Active Pair Visibility
The system SHALL identify active pairs that are stale, missing snapshots, or outside the active Alpha universe used by the worker selection loop.

#### Scenario: Active pair is stale
- **WHEN** an active pair has no recent snapshot according to the configured freshness threshold
- **THEN** the backlog reports the pair with state, token identity, last snapshot time, next refresh time, and whether it belongs to the active Alpha universe

#### Scenario: Legacy active pair is not in Alpha universe
- **WHEN** an active pair is not selected by the current Alpha-universe worker loop
- **THEN** the backlog reports it separately from retryable current-universe pairs so operators can archive or inspect it intentionally
