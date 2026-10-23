## Implementation

- [x] Trace all runtime loops, third-party requests, cache behavior, and frontend detail loading.
- [x] Separate OI and depth warm loops and align candidates with the visible scored list.
- [x] Rotate OI warmup through scored tokens beyond the first 50 while retaining first-page priority.
- [x] Preserve last successful detail data, back off failures, and persist recent OI/depth successes.
- [x] Cache Alpha discovery, validate its response, and avoid skipping a token on the first transient DexScreener error.
- [x] Continue the normal DexScreener batch after an individual failure and retry only due failed tokens.
- [x] Check recent market snapshots and shared detail caches before scheduled external calls.
- [x] Remove the unused simulated-trade tracker from combined mode.
- [x] Add focused checks for candidate selection, stale fallback, and cache restoration.
- [x] Validate release build and live HTTP endpoints after restart, including restored OI/depth timestamps and `/api/status` job counts.
