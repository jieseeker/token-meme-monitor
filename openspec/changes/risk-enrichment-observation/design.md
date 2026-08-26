## Context

Risk metadata can prevent obviously fragile tokens from being over-trusted, but it is easy to overfit to noisy provider data. The system should first collect and display risk signals with transparent source metadata. Once the data proves reliable, a later change can decide whether and how to feed it into scoring.

## Goals / Non-Goals

**Goals:**

- Normalize risk metadata from optional providers.
- Preserve source, fetch time, confidence, and failure information.
- Make risk state visible in reports and dashboard details.
- Keep scoring behavior unchanged in observation mode.

**Non-Goals:**

- Build a rug-pull classifier.
- Make risk providers mandatory for worker operation.
- Change alert thresholds or p4 probabilities.
- Guarantee chain-specific coverage for every token in the first pass.

## Decisions

- Risk snapshots will use a normalized schema with raw provider payload retained separately when practical.
  - Rationale: operators need both stable fields for UI and raw data for audits.

- Provider failures will be first-class records, not silent misses.
  - Rationale: absence of risk data is itself operationally meaningful.

- Risk refresh will use TTL and backoff.
  - Rationale: provider calls should not dominate normal market-data refresh cycles.

- Observation mode is mandatory for the first implementation.
  - Rationale: displaying data before using it for scoring prevents accidental strategy regressions.

## Risks / Trade-offs

- Providers can disagree or change response formats. Adapter tests should lock expected normalization behavior.
- Risk data may be sparse for new or low-liquidity tokens. UI should distinguish `unknown` from `low risk`.
- Raw payload retention can increase database size; retention policy should be coordinated with data lifecycle work.
