## ADDED Requirements

### Requirement: Predictions can be backtested without future leakage
The system SHALL provide a CLI backtest that evaluates test events with calibration data available only before each event.

#### Scenario: Backtest runs on stored prediction dataset
- **WHEN** the user runs `backtest-predictions`
- **THEN** the system loads stored prediction dataset rows, de-duplicates nearby events, splits them chronologically, and evaluates the test segment with expanding past-only calibration

#### Scenario: Report is written
- **WHEN** the backtest completes
- **THEN** the system writes JSON and Markdown reports with total rows, usable events, train events, test events, aggregate metrics, and opportunity-score bucket metrics

### Requirement: Backtest reports quality-aware metrics
The system SHALL expose sample eligibility and price-quality filtering in prediction backtest reports.

#### Scenario: Price divergence filter is configured
- **WHEN** the user runs backtest with `--max-price-divergence-pct`
- **THEN** rows with known absolute divergence above the threshold are excluded and counted in the report quality summary

#### Scenario: Horizon sample coverage differs
- **WHEN** a test event lacks enough samples for a horizon
- **THEN** that event does not contribute to that horizon's actual hit-rate denominator
