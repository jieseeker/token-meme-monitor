## ADDED Requirements

### Requirement: Prediction outcomes include label-quality metadata
The system SHALL store source and price-quality metadata with every prediction outcome row.

#### Scenario: GeckoTerminal hourly outcome is computed
- **WHEN** an outcome is computed from GeckoTerminal hourly OHLCV
- **THEN** the stored outcome includes `outcome_source`, `base_price_source`, `base_price_usd`, `gecko_base_close_usd`, `price_divergence_pct`, and `quality_flags_json`

#### Scenario: Existing database is initialized
- **WHEN** the repository initializes an existing SQLite database without the new outcome columns
- **THEN** the repository adds the missing columns without dropping existing rows

#### Scenario: Existing outcome rows are refreshed for quality metadata
- **WHEN** the user runs `refresh-prediction-outcomes --refresh-missing-quality`
- **THEN** mature prediction outcomes with unknown quality metadata are eligible for recomputation

### Requirement: Outcome quality flags identify partial or divergent labels
The system SHALL mark prediction outcomes with quality flags when source coverage or base-price alignment is weak.

#### Scenario: Signal price diverges from GeckoTerminal base close
- **WHEN** the signal feature price differs from the GeckoTerminal base close by more than 10 percent
- **THEN** the outcome quality flags include `price_source_divergence_gt_10pct`

#### Scenario: Hourly sample coverage is incomplete
- **WHEN** an outcome has fewer than the configured usable samples for a horizon
- **THEN** the outcome quality flags include the matching partial horizon flag
