## 1. Data Quality Backlog

- [ ] 1.1 Add repository query helpers for mature missing outcomes, local snapshot outcomes, price-divergent external outcomes, partial horizon coverage, and refresh-skipped rows.
- [ ] 1.2 Add repository query helpers for stale active pairs, no-snapshot active pairs, and legacy active pairs outside the Alpha worker universe.
- [ ] 1.3 Add a report builder that returns backlog summary counts, calibration-ready counts, stale-pair counts, and refresh-throughput warnings.
- [ ] 1.4 Add unit tests covering missing outcome counts, local snapshot separation, price-divergence filtering, partial horizon classification, and stale active pair visibility.

## 2. Missed Gainer Analysis

- [ ] 2.1 Extend scheduled backtest analysis to group missed strong gainers by score band, prediction stage, signal state, and miss reason.
- [ ] 2.2 Include representative case context for each missed-gainer group without duplicating full dataset rows.
- [ ] 2.3 Add tests for low short-momentum misses, non-priority signal state misses, and grouped summary rendering.

## 3. Breakout Observation Queue

- [ ] 3.1 Define review-only breakout candidate criteria using existing prediction, feature, quality, and risk fields.
- [ ] 3.2 Add queue output to scheduled report JSON and markdown without changing signal scores, pair states, probabilities, or alerts.
- [ ] 3.3 Add dashboard/view-model support for displaying breakout observation candidates and their evidence.
- [ ] 3.4 Add tests proving queue membership does not mutate live scoring or alert eligibility.

## 4. Operations and Documentation

- [ ] 4.1 Update scheduled worker defaults or runtime configuration so outcome refresh throughput keeps pace with mature prediction volume.
- [ ] 4.2 Add a documented maintenance path for archiving or reviewing legacy active pairs that no longer belong to the Alpha universe.
- [ ] 4.3 Update backend and dashboard documentation with the new observation workflow and sample-size guardrails.
- [ ] 4.4 Run focused tests and a scheduled report generation pass, then record before/after health metrics.
