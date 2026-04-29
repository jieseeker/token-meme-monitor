## 1. Prediction Model

- [x] 1.1 Add failing tests for horizon-specific prediction scores
- [x] 1.2 Add `short_momentum_score`, `continuation_score`, and `breakout_score` to `PredictionResult`
- [x] 1.3 Compute the three scores from calibrated probabilities and risk
- [x] 1.4 Keep `opportunity_score` as the primary short-momentum compatibility score

## 2. Persistence and Reports

- [x] 2.1 Add additive SQLite columns to `signal_predictions`
- [x] 2.2 Persist and query the new score columns
- [x] 2.3 Include new score columns in dataset exports
- [x] 2.4 Update backtest bucketing/report output to use horizon scores

## 3. Dashboard

- [x] 3.1 Use short momentum score for candidate strength and representative-pair selection
- [x] 3.2 Update prediction tab copy and labels to show separate horizons
- [x] 3.3 Keep old-row fallback to `opportunity_score`

## 4. Verification

- [x] 4.1 Run focused prediction/database/dashboard tests
- [x] 4.2 Run affected CLI/backtest/outcome tests
- [x] 4.3 Run full unittest suite
- [x] 4.4 Rebuild local predictions and rerun walk-forward backtest
