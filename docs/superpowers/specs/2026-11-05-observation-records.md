# Persistent observation records

Accepted: automatic observation list plus lasting original-list marker, seven-day retention, one row per token, immutable trigger price/time/features, appended repeated events, current same-pool price/change/max gain/maximum peak-to-trough drawdown. Expired records remain visible and lose live priority.

Reuse prelaunch event storage; no second favorites store. Add a mutually exclusive observation-only filter beside the existing favorites view. Observation view includes historical archives no longer currently monitored, explicitly labeled as archives. Normal main list remains current Binance-contract Alpha only. Search and refresh preserve archive selection.

Track each event in its original pool using incremental indexed source reads. Do not compare original pool price with weighted cross-pool current quotes. Prices, extrema and drawdown are sampled observations, gross of costs; stale prices are labeled last-known. Persist tracking cursor/peak across restarts and keep frozen trigger context. Expose grouped seven-day records in /api/prelaunch even when fresh live items are unavailable. On transient fetch failure retain previously loaded history, never resurrect live eligibility.

Existing flat layout and palette; compact neutral lasting marker; no list tiers or manual detail expansion. All event rows render in a horizontally scrollable table. Current eligibility and initial gate state remain distinct.

Verify immutable anchors, incremental extrema/drawdown, original-pool isolation, restart/expiry retention, API stale-report archive retention, list dedup/sorting/search, offline archive selection, refresh preservation and live expiry.
