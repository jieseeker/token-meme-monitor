# Live Prelaunch Implementation Plan

**Goal:** independently timestamp and evaluate prelaunch research observations without promoting the rejected historical rule into a buy signal.

**Spec:** ../specs/2026-11-05-prelaunch-live.md

**Architecture:** standard-library Python minute worker, read-only main SQLite, separate seven-day atomic JSON state/report; fail-closed Rust API; existing flat page styles.

- [x] Lifecycle/quote/risk/market tests fail first; implement restart-safe edges and same-pool delayed outcomes in scripts/prelaunch_monitor.py.
- [x] API stale-file regression fails first; implement /api/prelaunch with a120s freshness contract.
- [x] Page regression fails first; implement independent observation display, expiry/order and separate research outcomes. Fetch all current V2 summaries.
- [x] Register bounded minute worker in existing installer and deployment status list.
- [x] Full relevant checks: 160 Rust tests, Python research/monitor checks, Node page regression, deploy script regression, diff/format checks. Impeccable detector returned no findings. Review covered state, source freshness, missing endpoints and original strong rules.
- [x] Commit/push/deploy; cloud running f31d774. tmm-prelaunch.service active; API available; native Chrome AX and screenshot confirmed event expiry and separate outcome table. State persisted across restart.

## Cloud acceptance 2026-11-05 20:47 CST

First live check exposed an overly tight120s historical sampling tolerance and a costly full-window read. Fixed to distinct as-of historical observations no older than5m (aligned with the research export), while the current sample remains <=2m. Queries now use pool/time indexes. Real probe returned30 complete feature windows versus zero before, and recognized2 blocked events. Failed depth-cache stale markers are propagated and rejected.

After final restart the next two scans preserved2 prior events and increased to6 as new conditions appeared. Latest coverage:43 monitored tokens,20 fresh reference pools,19 complete history windows. All events were risk/market blocked or expired; no observing candidate was forced through risk_off. Endpoint results are still waiting for maturity and are hypothetical, not validated profit. Future scans expose coverage so missing data cannot masquerade as a quiet market.
