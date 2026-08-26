## Context

The monitor already stores `signal_predictions` and mature `signal_prediction_outcomes`. Current calibration can use those rows, but the system cannot distinguish clean external OHLCV labels from fallback labels or detect base-price divergence between DexScreener features and GeckoTerminal candles. Backtesting also needs a path that does not evaluate historical predictions with calibration data from the future.

## Goals / Non-Goals

**Goals:**

- Preserve label-quality metadata with each prediction outcome.
- Keep existing SQLite databases compatible through additive migrations.
- Provide an event-level expanding walk-forward backtest report.
- Make output usable for deciding whether 2h, 6h, or 24h probabilities are reliable.

**Non-Goals:**

- Replace the probability model. At the time of this change the model was `p3`; the current runtime model is documented in `docs/backend-core-logic.md`.
- Train a local ML model.
- Change dashboard sorting or alert thresholds in this change.
- Delete or rewrite legacy outcome rows.

## Decisions

- Store quality data directly on `signal_prediction_outcomes`.
  - Rationale: the outcome row is the durable label consumed by calibration, export, and reports.
  - Alternative considered: store quality in `raw_json`; rejected because queries and exports need first-class columns.

- Use additive SQLite migrations during `MonitorRepository.initialize()`.
  - Rationale: local users already have existing databases; `CREATE TABLE IF NOT EXISTS` alone cannot add columns.
  - Alternative considered: separate manual migration command; rejected because this project runs primarily as a local tool.

- Keep signed `price_divergence_pct`.
  - Rationale: signed values show whether the signal feature price is above or below GeckoTerminal close. Filters use `abs(value)`.

- Use expanding walk-forward over de-duplicated events.
  - Rationale: frequent snapshots from one token should not dominate reliability metrics, and each test prediction should only use prior outcomes.
  - Alternative considered: one static holdout calibration; rejected because it is less representative of live operation as outcomes accumulate.

## Risks / Trade-offs

- Existing old outcome rows will have `unknown` source fields until refreshed or rebuilt -> reports keep them but can surface missing quality.
- Expanding calibration rebuilds are slower than one static holdout -> acceptable because de-duplicated event counts are much smaller than raw signal rows.
- Price divergence is only available when GeckoTerminal base close exists -> reports count known divergence separately and do not drop unknown rows unless a filter can be applied.

## Migration Plan

- `MonitorRepository.initialize()` adds missing columns to existing SQLite databases.
- New outcome refreshes populate the new fields.
- Existing rows remain readable with default values.
- Rollback is code-level only; additive columns can remain unused safely.
