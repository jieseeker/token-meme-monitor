## Context

The current p4 prediction stack already records predictions, computes mature outcomes, filters low-quality labels, and runs expanding walk-forward reports. The latest local health checks showed that mature outcome gaps can accumulate faster than the scheduled worker's default refresh limit, while high-score prediction buckets remain too sparse for confident weight changes. Scheduled reports also show strong 24h winners that were not elevated by short-momentum scoring.

## Goals / Non-Goals

**Goals:**

- Make outcome coverage and label quality gaps visible as an explicit backlog.
- Explain missed strong gainers with stable, repeatable dimensions instead of ad hoc inspection.
- Add a review-only 24h breakout queue that can collect evidence before any scoring or alert changes.
- Keep reports and dashboard views aligned with the same repository-level query outputs.

**Non-Goals:**

- No live alert threshold changes.
- No p4 probability formula, calibration bucket, or empirical raise/lower rule changes.
- No automated trading decisions.
- No new external data provider requirement.

## Decisions

1. Keep the change observation-first.

   The high-score sample set is too small to support aggressive weight tuning. The new outputs should surface evidence and candidate cases, while existing short-momentum scoring remains the source of live alert decisions.

2. Reuse prediction dataset rows as the source of truth.

   The prediction dataset already joins signal, prediction, outcome, feature, and token metadata context, including restored compacted features. Adding separate pipelines would create drift between dashboard, scheduled reports, and CLI exports.

3. Treat data-quality backlog as a first-class report section.

   Health currently exposes only aggregate missing outcome counts. A backlog should separate missing mature outcome rows, local snapshot labels, price-divergent GeckoTerminal labels, partial horizons, and stale active pairs so operators can see whether accuracy is limited by labels or model behavior.

4. Build breakout candidates separately from alert candidates.

   The missed-gainer problem is mostly about 24h realized moves, while the primary opportunity score is tuned to short 2h momentum. A separate queue avoids conflating long-horizon discovery with live short-horizon alerts.

## Risks / Trade-offs

- Sparse high-score data can still produce noisy recommendations -> require minimum sample counts and label all outputs as review-only.
- Large local SQLite datasets make full-report generation expensive -> prefer repository queries that pre-aggregate where possible and keep dashboard payloads bounded.
- Breakout candidates may include structurally risky or late-stage tokens -> include risk flags, stage, drawdown probability, and overextension reasons in each queue item.
- Raising scheduled refresh limits can increase external API calls -> make limits configurable and report skipped rows separately.
