## 1. Risk Snapshot Model

- [x] 1.1 Add failing tests for normalized risk snapshot persistence and retrieval
- [x] 1.2 Add storage for provider name, source token id, fetched time, TTL, confidence, normalized fields, raw payload reference, and failure reason
- [x] 1.3 Add migration or bootstrap behavior for existing SQLite databases

## 2. Provider Adapters

- [x] 2.1 Add failing adapter tests for successful normalization, missing fields, provider failure, and timeout/backoff behavior
- [x] 2.2 Implement optional provider interface and at least one disabled-by-default adapter or fixture provider
- [x] 2.3 Add configuration for enabling providers and setting TTL/backoff values

## 3. Worker Integration

- [x] 3.1 Add failing tests that risk refresh does not block normal pair refresh
- [x] 3.2 Refresh risk snapshots for tracked tokens using TTL-aware scheduling
- [x] 3.3 Persist provider failures with enough detail for health and dashboard diagnostics

## 4. Observation Surfaces

- [x] 4.1 Add report rendering for observation-only risk badges and unknown-risk state
- [x] 4.2 Add dashboard read models for risk summary and raw provider diagnostics
- [x] 4.3 Document that risk metadata is not yet used for scoring or alert suppression

## 5. Verification

- [x] 5.1 Run focused risk, worker, report, and dashboard query tests
- [x] 5.2 Run the full unittest suite
- [x] 5.3 Verify behavior with providers disabled
