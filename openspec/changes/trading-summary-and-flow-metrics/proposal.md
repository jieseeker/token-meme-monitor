## Why

The detail page exposed many metrics but did not answer a trader's first questions: what the current structure means, what would confirm it, what would invalidate it, and whether money is actually flowing in. The old `1h 净买入` metric was only a count difference and could mislead when small buys outnumbered one large sell.

## What Changes

- Add a trading summary above the first-card metrics: structure stage, current action, trigger, invalidation, 1h turnover, and data age.
- Replace count-based `1h 净买入` with `1h 大额净流入`, calculated from deduplicated large trades in the last hour.
- Calculate 1h turnover as `volume_h1 / liquidity_usd`.
- Add approximate USD value to large token transfers using the current price.
- Keep the detailed large-trade list on a 24-hour window and clearly label the one-hour summary.
- Add a local watchlist with a detail-card toggle and a “只看自选” list filter.

## Impact

The change affects the dashboard detail card, token list, and frontend tests. No backend API contract changes.
