# Signal quality comparison

## Intent and authority

Improve the evidence for choosing B filters, retaining A observation / B primary / C second start. The user delegated routine design, implementation, replay, review, commit and cloud delivery; no repeated design approval is required. This is the coordinator's design choice, not a user-approved numerical calibration.

## Approaches

1. Tighten live thresholds now: immediate fewer signals, but no trustworthy baseline establishes a benefit. Rejected.
2. Extend the existing read-only replay with fixed B cohorts and sampled outcome counts. Chosen: the smallest reversible comparison with no extra requests or scheduling changes.
3. Add OI, wallet attribution, or an external backtest platform. Deferred: mapping/coverage and additional collection exceed this experiment.

## Exact experiment

- Preserve production engine, source selection, A/B/C timing, risk classification, cache, worker and seven-day retention. No new dependencies or tables. No execution or notifications.
- Extend existing 15m/1h/4h sampled endpoints with positive/negative/flat counts and `positive_sampled_return_rate_pct = positive / eligible * 100`; missing and censored remain separate, zero eligible returns null. This is a sampled direction rate, not trading win rate or net profitability.
- `b_filter_comparison` uses independent cohort labels on the same original B events and endpoints. `all` is all structural B events. All other cohorts require contemporaneous risk `PASS`; BLOCK/HOLD/WARN remain in original structural totals. Source freshness remains unverified and opportunity eligibility remains zero.
- `pass`: PASS B baseline.
- `pass_recent_volume`: current m5 >0, current h1 >0, m5 >= h1/12, and current m5 >=80% of the m5 at the original B crossing. Crossing m5 must be a positive known same-pool prior sample at `b_crossed_at`; all samples at that engine second must agree, otherwise the filter is unknown. Never sum rolling windows or treat overlapping m5 observations as independent flows.
- `pass_near_breakout`: confirmation price is at most 3% above the frozen B breakout line (`reference_upper *1.005`).
- `pass_both`: both fixed candidates. Missing candidate evidence never passes. Report independent recent-volume `pass/fail/unknown` diagnostics for risk-PASS B.
- PASS distance buckets: `0–3%`, `3–5%`, `5–8%`, `>8%`. Lower boundary exclusive, upper inclusive, with zero included in the first bucket. No threshold search.
- Include the same cohorts by event UTC date, fixed solely by event time. They support chronological comparison; date grouping alone does not certify an untouched holdout or statistical significance. All-period empty cohorts are explicit zero counts.
- Freeze all cohort membership at the B event, before future endpoints. One pending event feeds all matching cohorts, avoiding duplicate label queues and changing neither B nor C.
- Keep each token's existing bounded replay history/queue; at most the fixed cohort count per event and eight UTC dates in a seven-day window. No per-address output or long-term raw archive.

## Evaluation and promotion

Run strict and separately labeled legacy-proxy modes twice on consistent local/cloud copies; verify source SHA256/schema/counts unchanged, deterministic JSON, original stage counts unchanged versus the base binary. Compare sample count/coverage/mean/direction rate per cohort and date, without promoting on sparse or non-comparable samples. Old data cannot recover provenance. New strict metadata still cannot certify upstream freshness or runtime delivery parity. Do not invent fills, costs, intraperiod drawdown, path-based TP/SL or exact execution venue depth from unavailable historical inputs.

Deploy the reviewed offline-report capability using the existing Git delivery path and verify service/old APIs/diagnostic mode. Runtime signal rules stay at their current shadow version. If evidence is insufficient or filters underperform, explicitly retain baseline; an apparent in-sample improvement is not approval to change production rules.
