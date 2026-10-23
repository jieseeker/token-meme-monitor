## Why

The detail page waits for OI and pool depth because the old warm loop covered only five tokens and waited for each pool before moving on. A failed third-party call replaced a successful cached value and retried every minute. The market worker also fetched the Alpha list on every scan and could skip a token after one transient DexScreener failure.

## What Changes

- Keep Alpha discovery, DexScreener market snapshots, signal calculation, and SQLite writes in one dependent scan chain.
- Give OI and pool depth independent background warm loops, using the same cached endpoints as on-demand detail requests.
- Warm the first 50 scored tokens as the priority OI group, then rotate through the remaining scored tokens in batches of 50; warm depth for each pool of the first 50 tokens.
- Preserve the last successful result on refresh failure, back off repeated failures, and restore recent OI/depth results from SQLite after restart.
- Stop running the unused simulated-trade outcome tracker in combined mode.

## Impact

The changes affect the Rust market worker, dashboard cache and background scheduling, plus one small SQLite cache table. The HTTP response shape remains compatible; stale successful data includes `stale` and `last_error`.
