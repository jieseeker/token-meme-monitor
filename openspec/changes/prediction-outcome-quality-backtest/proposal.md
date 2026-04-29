## Why

Prediction probability is only useful if the historical labels are trustworthy and the evaluation avoids future leakage. The project now has enough mature prediction outcomes to start calibrating probabilities, so the backtest path needs explicit price-quality metadata and chronological validation.

## What Changes

- Add price-source quality metadata to `signal_prediction_outcomes`.
- Track whether outcome labels came from GeckoTerminal hourly OHLCV or local snapshot fallback.
- Record the signal base price, GeckoTerminal base close, signed price divergence, and quality flags.
- Add an expanding walk-forward backtest command that rebuilds predictions from past-only calibration rows.
- Export JSON and Markdown reports with bucket-level predicted probability, actual hit rate, sample counts, distinct token counts, and quality exclusions.

## Capabilities

### New Capabilities

- `outcome-quality-labels`: Outcome labels carry source, base-price, divergence, and quality metadata for filtering and diagnostics.
- `prediction-walk-forward-backtest`: Stored prediction history can be evaluated with chronological, event-level backtesting without using future outcomes for test predictions.

### Modified Capabilities

- None.

## Impact

- Affects `signal_prediction_outcomes` schema and automatic SQLite migration.
- Affects outcome refresh, prediction dataset export, and new prediction backtest CLI.
- No new third-party dependencies.
