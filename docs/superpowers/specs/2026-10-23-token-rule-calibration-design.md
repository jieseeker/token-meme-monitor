# Token rule calibration

## Intent

Make the Alpha candidate score and risk labels describe one BSC token across its selected PancakeSwap and Uniswap pools. Keep the dashboard as a research aid, not a trade recommendation.

## Approaches

- **Chosen:** evaluate the existing signal engine once per token using an aggregate of the selected pools, then write the same score and reasons beside each pool snapshot. This preserves the current SQLite/API shape and makes list and detail agree.
- Recompute scores on each HTTP request: rejected because it moves background work into page loading and duplicates the worker's rules.
- Add a separate token signal table: rejected because it adds a migration and parallel scoring path for this change.

## Rules

- Sum selected pools' liquidity, rolling volume, and buy/sell counts; weight price changes by liquidity. Use the largest pool's FDV and market cap. Skip a pool with no valid pair address. Do not count the same activity twice: the extreme volume/liquidity rule adds no extra score.
- Website/social listing, paid DEX boosts, and an absolute holder count are context, not score bonuses. Keep Alpha's own activity score as a modest source-specific signal. Larger liquidity must not score less than the medium band.
- Describe buy/sell dominance as **trade-count** dominance. Collapse low and near-zero liquidity into one visible severity. Treat missing provider values as data status rather than a token safety verdict.
- Add token risk observations using existing data only: 1% down-side sell amount below $10,000 is `thin_downside_depth`; 1% up-side buy amount at least 3 times the down-side amount is `upside_depth_heavy`. Require depth results no older than 5 minutes and coverage of at least 90% of selected liquidity. The amounts are simulated price impact, not orders.
- Add `liquidity_withdrawal` when the same selected pool set has snapshots 15–25 minutes ago and combined liquidity has fallen at least 25%. If any comparison pool is missing, show no tag. Neither new risk observation changes the candidate score.
- Historical rows may contain retired labels. The page suppresses those labels until all tokens have fresh signals.
- A signal older than 5 minutes loses its 5-minute volume/buy-dominance tags, the price-trend tag that depends on 5-minute movement, and 5-minute risk flags in both list and detail. Its score removes those bonuses conservatively; an old sell-pressure penalty may be released but cannot raise the score above its original value. An old prediction score falls back to this adjusted signal score. The page labels the result as a conservative estimate.

## Verification

- Tests cover aggregated scoring, no double bonus, same-pool liquidity comparison, depth freshness/coverage/asymmetry, and old-label rendering.
- Run Rust and frontend suites, then inspect the local dashboard with live DNA/BTW and a token with fresh depth. Cloud remains unchanged.
