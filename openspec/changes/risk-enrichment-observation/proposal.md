## Why

The current pipeline focuses on market, liquidity, social, and prediction behavior. It can surface promising tokens, but it does not yet capture structured token risk signals such as holder concentration, liquidity lock state, ownership controls, tax flags, or external risk-provider confidence. Those signals are valuable, but provider quality varies, so the first pass should observe and display risk metadata without changing scoring.

## What Changes

- Add a normalized risk snapshot model with provider metadata, timestamps, confidence, and failure reasons.
- Add optional risk provider adapters behind configuration so the pipeline degrades cleanly without API keys.
- Store risk snapshots for tracked tokens with TTL-aware refresh behavior.
- Display risk summaries in reports and dashboard views in observation mode.
- Keep risk data out of p4 scoring and alert gating until a later promoted change.

## Capabilities

### New Capabilities

- `risk-enrichment`: optional risk metadata ingestion, storage, and display.

### Modified Capabilities

- Existing report and dashboard views can show observation-only risk badges.

## Impact

- Affects database schema or cache storage, token refresh orchestration, report rendering, dashboard views, configuration, and documentation.
- Does not automatically suppress alerts, alter scores, or require any single external risk provider.
