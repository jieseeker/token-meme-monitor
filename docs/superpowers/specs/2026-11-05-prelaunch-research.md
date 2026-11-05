# Prelaunch research

User objective: identify contract-listed Binance Alpha BSC tokens with rally potential before a material price move, using historical evidence and net profitability.

Use a new offline research path. Existing A state is not a label for actual rallies. Reuse snapshot price, volume, transactions and liquidity. No new dependencies. No account/order integration.

Export seven days of five-minute observations from cloud SQLite read-only, one reference pool chosen by contemporaneous liquidity per token and bucket. Require contemporaneous Alpha futures eligibility; retain historical delisted tokens in research. Never pick a pool using its future liquidity. Exclude discontinuous pool/time windows. Bucket timestamps represent completed buckets; next bucket is the simulated entry, capped at +5% from alert price.

Prelaunch eligibility: preceding hour return -8% to +5%, preceding 15m return -3% to +3%, price at most 5% above preceding-hour low, liquidity >=$50K, no liquidity-withdrawal/near-zero hard flag, market not risk_off. These are research assumptions, not a validated buy rule.

Primary outcome: +20% before -8% within four hours after delayed entry, timeout at observed price. Secondary +10% outcome. Features use only past observations. Compare modest/sustained volume growth, buy share, price compression and stable/increasing liquidity; finite single/pair rules, no unbounded search.

Chronological 60/20/20 train/validation/test split, purge four-hour forward windows at boundaries. Select using training and validation only, inspect final test once. Report events, tokens, fees, exit rules, mean/median, win rate, PF, sampled drawdown, capture/lead time and missing coverage. Event repeats cannot create overlapping positions in one token. Report all-feature associations only on training data, plus winner concentration and best-token-removed sensitivity.

Production constraints remain: no B buy upgrade, A strong window <=5m, real $100/$1K slippage gates, no risk_off buy, main list only contract-listed Binance Alpha. A research candidate is not a strong-confirmation signal. Historical actual slippage/routing and provider source times are unverified; no profit claim.

Only deploy a changed discovery rule if held-out evidence supports it; otherwise deliver the reproducible research/report and explicit rejection rather than relabel an unprofitable rule. This user authorization covers routine design and implementation decisions.
