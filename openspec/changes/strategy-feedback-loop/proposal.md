## Why

The project can now build p4 predictions, backfill outcomes, run walk-forward backtests, and generate scheduled reports. Those pieces show whether predictions worked, but they do not yet produce a durable strategy feedback artifact that explains which slices are improving, which slices are weak, and which threshold changes are worth considering.

## What Changes

- Add versioned strategy feedback runs that summarize prediction quality by stable slices.
- Compute slice metrics across horizons, score bands, liquidity, market cap, age, and quality flags.
- Generate tuning candidates as recommendations, not automatic strategy mutations.
- Surface the latest feedback summary in scheduled reports and dashboard views.
- Document the feedback loop and verification workflow.

## Capabilities

### New Capabilities

- `strategy-feedback`: versioned prediction/outcome analysis and recommendation generation.

### Modified Capabilities

- Existing backtest and scheduled report workflows can consume the latest strategy feedback summary.

## Impact

- Affects prediction outcome analysis, backtest reporting, scheduled report content, dashboard read models, and documentation.
- Does not change p4 scoring, alert thresholds, or worker alert decisions automatically.
