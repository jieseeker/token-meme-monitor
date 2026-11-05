# Live prelaunch research events

User authorizes repair following the historical research. Deliver independent live research observations, not a validated buy strategy. Historical 77-rule research failed; disclose this on the page.

Use existing snapshot features: prelaunch baseline from prelaunch_research, m5/liquidity >=1%, relative volume >=1.2. This is an explicitly unvalidated activity probe, not a historical winner. Snapshot and actual quoted depth must be fresh; risk PASS; market risk_on/neutral; no already-started B/C; $100 <=.30%, $1K <=.50%. No dependence on old A first_seen_at. Original A strong conditions remain distinct.

Trigger on the first valid condition edge. Five-minute event freshness, no sustained-condition refresh. Rearm only after five continuously observed quiet minutes and at least15 minutes since previous event; missing data never rearms. Pool changes end display eligibility, outcomes stay locked to the trigger pool. Persist restart-safe state and seven-day events in separate atomic JSON files, read market DB only, one process lock. Track blocked triggers too; initial blocked trigger cannot silently become eligible.

Research outcomes: paper entry first source sample 5-7m after trigger, skip if >5% above alert; same-pool 15m/1h/4h endpoints; hypothetical +20%/-8%/4h path and .3/.5% cost assumptions. Counts/denominators/missing outcomes explicit. No account or real orders.

Service runs every60s. API fails closed on missing/stale report. Flat existing page shows independent potential-observation strip and prioritizes only currently eligible fresh observations; expired/blocked events have explicit Chinese reasons. No new browser notification for an unvalidated probe. Original strong cohort metrics stay separate.

Validate event lifecycle, gates, persistence, pool changes and same-pool delayed outcomes; API stale gating; page rendering and ordering; regression suites, service deployment and real cloud data.
