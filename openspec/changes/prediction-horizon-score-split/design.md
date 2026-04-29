## Context

The latest local walk-forward backtest shows overall 2h probability is close to observed reality, while 24h outcomes remain sparse. A single opportunity score encourages over-reading weak long-horizon evidence.

## Goals / Non-Goals

**Goals:**

- Make 2h short momentum the primary dashboard sorting score.
- Preserve existing code paths that still read `opportunity_score`.
- Keep 6h and 24h visible but semantically weaker than the short-term score.
- Avoid a model rewrite or new dependency.

**Non-Goals:**

- Train a local ML model.
- Change alert thresholds.
- Remove existing probability fields.

## Decisions

- `opportunity_score` remains stored and equals `short_momentum_score` for newly generated predictions.
  - Rationale: many existing queries and UI paths already depend on it.

- Three new columns are additive and nullable.
  - Rationale: existing SQLite databases can migrate safely, and p3 rows can still render through fallback logic.

- Dashboard gate/sort uses `prediction_short_momentum_score` first, falling back to `prediction_opportunity_score`.
  - Rationale: current validation supports short-term scoring better than 24h scoring.

- Backtest buckets use `short_momentum_score`.
  - Rationale: bucket labels should match the primary score used by the dashboard.

## Risks / Trade-offs

- Existing p3 rows need `rebuild-predictions` to populate new score columns -> fallback keeps UI usable until rebuild finishes.
- 6h/24h scores may look precise despite low samples -> dashboard labels call them continuation/observation, not primary buy signals.
