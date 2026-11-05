# Live Prelaunch Implementation Plan

**Goal:** independently timestamp and evaluate prelaunch research observations without promoting the rejected historical rule into a buy signal.

**Spec:** ../specs/2026-11-05-prelaunch-live.md

**Architecture:** standard-library Python minute worker, read-only main SQLite, separate seven-day atomic JSON state/report; fail-closed Rust API; existing flat page styles.

- [x] Lifecycle/quote/risk/market tests fail first; implement restart-safe edges and same-pool delayed outcomes in scripts/prelaunch_monitor.py.
- [x] API stale-file regression fails first; implement /api/prelaunch with a120s freshness contract.
- [x] Page regression fails first; implement independent observation display, expiry/order and separate research outcomes. Fetch all current V2 summaries.
- [x] Register bounded minute worker in existing installer and deployment status list.
- [ ] Full relevant checks and review of state, source freshness, missing endpoints and original strong rules.
- [ ] Commit/push/deploy; verify service, API, actual page and report persistence across scans.
