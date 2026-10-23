## Task inventory and scheduling

| Task | Source | Trigger | Decision |
| --- | --- | --- | --- |
| Alpha token list | Binance Alpha | Attempt at configured minute interval | Keep with market scan because it supplies token addresses; retain last good list on failure and wait until the next interval before retrying. |
| Pool discovery and market snapshots | DexScreener | Rotating token scan | Check that every active pool has a recent snapshot before skipping a token call. Refresh tokens with an active 5-minute signal ahead of the normal scan after 120 seconds; keep the normal cursor moving. Queue only failed tokens for timed retries while the normal scan continues. HTTP 429 pauses this provider. |
| OI, large-holder ratio | Binance Futures | Independent background warm and detail fallback | Rank tokens by their highest pool score. Check the first 50 each round, followed by a rotating batch of up to 50 other scored tokens. Use each token's highest-liquidity pool as the cache key, matching the detail page; a slow exchange cannot hold up depth. |
| Global OI | CoinGecko | Shared with OI refresh | One cached response for all symbols; on failure keep the previous map and wait one minute before retry. |
| Pool depth | BSC RPC | Independent background warm and detail fallback | Prioritize all pools of the top five tokens each round, then rotate through ten pools from the rest of the visible list. Background uses one slot, leaving the second for an opened detail. |
| Market mark-price history | Binance Futures | Detail request, 60-second cache | Keep separate; chart-only data should not compete with critical OI warmup. |
| Large pool trades | GeckoTerminal | Detail request, five-minute cache | Keep separate; its provider and failure mode differ from depth. |
| Large token transfers | Binplorer | Detail request, five-minute cache | Keep separate; sample API does not provide a reliable whole-chain stream. |
| History cleanup | SQLite | Daily / short batches while backlog exists | Keep as database maintenance in worker. |
| Simulated-trade outcome tracker | SQLite | Previously every five minutes | Remove from combined runtime because no code opens simulated trades. |
| Health probe | DexScreener | Explicit health endpoint | Do not put outbound health probes in the regular scan path. |

## Failure policy

The detail cache deduplicates in-flight requests. Successful OI and depth results are saved in SQLite and restored at startup when less than 15 minutes old. A refresh failure keeps the last successful value for at most one hour and marks it stale; older results become unavailable. Consecutive failures retry after 60, 120, 240, 480, then 900 seconds. Unsupported or unavailable data uses the same backoff so it cannot hammer a provider. Alpha list refresh failure uses the prior list. DexScreener failures enter a per-token retry queue with a 30–300 second backoff; successful and still-fresh tokens are skipped. HTTP 429 pauses DexScreener for 60 seconds.

Successful market and OI data are fetched again only after their refresh window because prices, liquidity, and positions change. Within a batch, only a failed token is retried; completed tokens are not repeated. Alpha's endpoint returns one complete list, so a failed list refresh retains the previous successful list rather than retrying individual entries that the endpoint cannot address.

The market scan checks up to 20 tokens whose latest active signal contains `m5_buy_dominance` or `m5_volume_impulse`, then every token in the same ranked list the page displays. Active-signal tokens and the first 50 displayed tokens use a 120-second freshness threshold. Other displayed tokens become eligible after 240 seconds to leave a worker-cycle margin before the five-minute target; tokens outside the list use 900 seconds during ordinary discovery rotation. Only due tokens enter the priority queue, and each priority slot reduces the normal batch by one so the routine scan stays within its request budget. At least one normal slot, or a quarter of larger batches, remains available so the cursor continues moving even when many visible tokens are due. Failed-token retries remain additional work. The normal scan cursor advances only through its own slots. Thresholds make tokens eligible for the next worker cycle; they do not guarantee exact refresh times. Each cycle logs displayed-token age by the top-50 and remaining tiers, priority and discovery slots, and aggregate DexScreener request time. The 250 ms post-request spacing stays below the provider's published 300 requests per minute even if responses are instantaneous. `/api/status` reports OI and depth cache ages separately from market snapshots.

In-memory entries unused for an hour are evicted to keep long-running process memory bounded; the last 15 minutes of successful OI and depth remain recoverable from SQLite.

DexScreener's failed-token queue is process-local. A restart resumes from the persisted scan cursor and uses recent SQLite snapshots to skip completed tokens, but a failed token without a snapshot may wait until its next normal rotation. Persisting that queue is the upgrade path if restart recovery time becomes important.

OI and depth warm loops check the same in-memory cache as page requests before they invoke the detail endpoint. A page-triggered refresh therefore satisfies the scheduled job. `/api/status` reports OI and depth cache counts for cached, loading, stale, and unavailable entries without contacting third-party APIs.

## Capacity ceiling

V3 depth currently performs many RPC quote calls per pool. The background loop is intentionally serial to avoid saturating the public RPC; the current visible list has about 148 pools for 50 tokens, so refreshing all of them within five minutes is unrealistic. The top five tokens are revisited each round while the rest are rotated in batches of ten. Five minutes is the cache refresh threshold, not a guaranteed age for every pool. The selected token can still request a pool immediately using the reserved depth slot. If measured full-rotation time remains too high, the next step is to reduce RPC calls per V3 quote or use a dedicated RPC provider, rather than raise concurrency blindly.
