# Profit-oriented signal research

## Goal

Shift the real-time structure panel from risk-only waiting to a balanced trading workbench, and extend replay from endpoint direction rates to fixed-rule path profitability. The product remains research-only and does not execute trades.

## Decisions

- `fdv_liquidity_stretched` becomes a soft warning. Hard blocks remain liquidity near zero, explicit `BLOCK`, and unavailable liquidity.
- A confirmed first or second start is an executable research candidate when data is fresh, risk is not blocked, and price is 0–8% above its corresponding frozen breakout line.
- Replay path rules are fixed: 4h+5m window, +10% target, -10% stop, timeout at the next observed price, and a declared 2% assumed round-trip cost. Compare full +10% exit, no fixed target with -10% stop/timeout, and a 30%/30% ladder at +10%/+25% whose remainder exits at the greater of +8% or a 20% drawdown from the observed high. Output net mean return, wins/losses, and profit factor.
- Path results use observed snapshots only. They cannot infer intraperiod ordering, fills, actual fees, slippage, or position size, and never claim real profit.

## Verification

- Signal risk precedence and missing-price handling tests cover soft versus hard risk.
- Page checks cover executable B and C states.
- Replay tests cover target-first and stop-first outcomes and distinguish them from sampled win rate.
