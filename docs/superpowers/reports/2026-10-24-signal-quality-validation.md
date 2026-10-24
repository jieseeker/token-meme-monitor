# Signal quality comparison — 2026-10-24

## 结论

完成固定 B 筛选对照，未修改线上信号门槛。云端探索样本中，风险 PASS 基线10次，近期成交筛选保留6次；1小时采样上涨比例从5/10变为4/6，平均采样涨跌幅从−0.1286%变为+0.1029%。但15分钟上涨比例从3/10降至1/6，平均变化也变差。这是小样本、不同持有窗口的条件结果，未扣成本，不能宣称稳定胜率提升。3%距离条件没有筛掉任何 PASS 样本，当前无法验证其价值。严格模式 B/C 均无样本；保持影子运行，暂不晋级。

## Scope and exact identity

- Source code commit: `ad0e60f1c34ed9590efa146af2436c616872affa`; base `0811b2278321ef2d81839ad123f9d6108e583c15`. Later report-only commits are separate.
- Shared detector remains `signal_v2_shadow_v1`, min h1 USD15000, focus65. Offline comparison identity `b_quality_v1`, fixed m5/h1≥1/12, crossing-m5 retention≥80%, near-line extension≤3%.
- Only README, design/plan and offline replay changed. Production engine, worker, cache/API scheduling, OI, seven-day cleanup, frontend and notification/trading behavior are unchanged. No new dependency/table/provider requests.
- `positive_sampled_return_rate_pct` divides positive by eligible sampled endpoints; flat/negative/missing/right-censored are explicit. It is neither execution win rate nor net PnL. Filters are event-time labels on the same original B events, not a separate detector or alternate C lineage.
- Strict provenance remains row-level only; upstream source time, response completeness and exact runtime delivery parity are unverified. Proxy runs mix eligible old numeric rows and valid strict rows; old zero volumes remain unknown.

## Inputs and read-only evidence

Both copies were created with SQLite online backup from read-only source connections. Runs use READ_ONLY with one read snapshot; source-copy hashes, full sqlite_master schema digests and every table count match before/after. No production database replacement, migration or historical rewrite was performed by this experiment. Temporary raw copies are deleted at closure; these aggregates/hash identities remain.

| Copy | Range UTC | Rows | Strict accepted | A/B/C strict | A/B/C exploratory |
| --- | --- | ---: | ---: | --- | --- |
| local | 2026-10-21T23:16:36.045143Z – 2026-10-24T02:49:53.490403Z | 193101 | 0 | 0/0/0 | 33/0/0 |
| cloud | 2026-10-22T09:00:27.599928798Z – 2026-10-24T02:48:00.966962938Z | 345016 | 12716 | 10/0/0 | 98/25/1 |

The cloud strict rows span 2026-10-24T01:24:15.394537626Z–02:48:00.966962938Z, only about84minutes, insufficient to establish the detector's hourly background. Zero strict B is not evidence of zero market opportunities. Neither dataset contains seven days. Counts above are events, not independent tokens.

- local: file SHA256 `53924d13262c5baf1f2eda1b46303c8efca1c463cceeec0b2c346e010d752829`, schema SHA256 `72ce75acc71736083476b25fb5c25b48dd20573c15ce2be67048a117eedf88b1`. All 24 table counts unchanged; snapshots/signals 193101/193101.
- cloud: file SHA256 `494ac9ea676708fff16fd4ab3ea548981592feccde01a1902c2cee6e95966d7a`, schema SHA256 `b6eb846fad2b2768b4c6178e2630a54ac60e8bdc65bd48795888f4ed5427619c`. All 11 table counts unchanged; snapshots/signals 345016/345016.

## Cloud exploratory B comparison

All rows below are exploratory, source-time-unverified, with zero opportunity-eligible events. `all` includes risk-blocked structural events; `pass` and candidate cohorts require contemporaneous PASS. Never compare candidate ratios against all-risk totals as if risk filtering were a new change.

| Cohort | Horizon | Events | Eligible | Positive | Up % | Mean sampled % | Missing | Censored |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all | 15m | 25 | 25 | 12 | 48.0000 | 0.9896 | 0 | 0 |
| all | 1h | 25 | 24 | 10 | 41.6667 | 1.2492 | 0 | 1 |
| all | 4h | 25 | 20 | 7 | 35.0000 | 1.5108 | 0 | 5 |
| pass | 15m | 10 | 10 | 3 | 30.0000 | -0.1988 | 0 | 0 |
| pass | 1h | 10 | 10 | 5 | 50.0000 | -0.1286 | 0 | 0 |
| pass | 4h | 10 | 8 | 2 | 25.0000 | 0.6375 | 0 | 2 |
| pass_recent_volume | 15m | 6 | 6 | 1 | 16.6667 | -0.3046 | 0 | 0 |
| pass_recent_volume | 1h | 6 | 6 | 4 | 66.6667 | 0.1029 | 0 | 0 |
| pass_recent_volume | 4h | 6 | 5 | 2 | 40.0000 | 1.7919 | 0 | 1 |
| pass_near_breakout | 15m | 10 | 10 | 3 | 30.0000 | -0.1988 | 0 | 0 |
| pass_near_breakout | 1h | 10 | 10 | 5 | 50.0000 | -0.1286 | 0 | 0 |
| pass_near_breakout | 4h | 10 | 8 | 2 | 25.0000 | 0.6375 | 0 | 2 |
| pass_both | 15m | 6 | 6 | 1 | 16.6667 | -0.3046 | 0 | 0 |
| pass_both | 1h | 6 | 6 | 4 | 66.6667 | 0.1029 | 0 | 0 |
| pass_both | 4h | 6 | 5 | 2 | 40.0000 | 1.7919 | 0 | 1 |

Recent-volume evidence among PASS events: {"fail": 4, "pass": 6, "unknown": 0}. PASS distance buckets: pass_distance_0_3pct=10, pass_distance_3_5pct=0, pass_distance_5_8pct=0, pass_distance_gt8pct=0.

All10 PASS baseline events and all6 recent-volume events belong to UTC2026-10-23; UTC2026-10-24 has zero PASS B. Thus there is no usable later-day holdout for these filters. The6-event sample retains60% of baseline opportunities; it removed three negative and one positive1h outcome in this dataset. That is retrospective description, not a guaranteed future benefit. Four-hour eligible subsets differ because of censoring; their means are not equal-cohort comparisons. Two positive4h endpoints survive the filter, and one high return can dominate a mean.

Current evidence does not justify tightening distance limits or raising general score/liquidity thresholds. Historical execution-market depth is not available in these rows; do not substitute today's depth or reject exchange-dominant tokens merely for a thin chain pool. OI/whale attribution stays outside this test.

## Verification and review

- Initial7 new regression tests were written first:6 behavior assertions failed for absent features; one initial UNIQUE fixture error was corrected, then its intended behavior assertion failed. Implementation passed19 focused tests /127 full tests.
- Independent fresh whole-branch review at02a8293 found one Important issue: positive-price engine history omitted a missing-price conflicting crossing observation. The real SQLite regression reproduced recent-volume pass1 versus expected0 without changing B timing. Fix reads bounded raw same-pool crossing-second evidence independently of detector history.20 focused /128 full Rust tests pass, zero failed/ignored. The helper caps returned neighboring rows at4096 and marks overflow/ambiguous/missing crossing m5 unknown; no extra runtime query.
- Review fix commit ad0e60f; coordinator-owned RED→GREEN and full-suite verification per executing-plans, no extra re-review. Fresh Node detail and deployment-shell checks exited0; locked release build succeeded without warnings; whitespace clean. Binary/database/actual configuration files are excluded from Git; credential-marker scan on this branch's diff found0 matches.
- Both modes ran twice per copy after the fix, byte-identical JSON. A separately built base binary from unchanged0811b22 production code supplied original A/B/C events, first/last times, risk/data counts and endpoints. All original fields match; added directional fields and B-comparison counters are excluded only from the compatibility comparison. Input/coverage and denominator identities pass. Main-merge Rust suite128/128 passed.
- No browser run: UI unchanged. No path-based take-profit/stop-loss simulation, costs/fills/slippage estimate, case-asset identity proof, independent holdout result, significance/independence claim, arbitrary load or long-term capacity benchmark.

Elapsed totals (two candidate plus one base replay per dataset/mode; not a production benchmark):

- local: strict 3.813s, proxy 6.930s.
- cloud: strict 7.303s, proxy 16.647s.

## Coordinator rulings

- Ruling: Prior native managed worktree writes are known sandbox-denied; use an independent feature branch in the original checkout without retrying protected worktree operations — avoids the same environment failure — cost: weaker directory isolation, explicit file scope and commits required.
- Ruling: Implement fixed offline B cohorts before any live threshold promotion — current historic provenance and coverage cannot validate a strategy gain — cost: this delivery improves evaluation capability but cannot promise an immediate win-rate change.
- Final: Ruling: Real-data parity and source-integrity evidence remain coordinator-owned — rerun strict/proxy twice plus base binary after the fix, not inferred from reviewer approval — cost if wrong: a validation-harness blind spot could invalidate counts.
- Final: Ruling: Deployment/rollback/source identity stay explicit target-host gates — reviewer had no production access; preserve existing unit/env/proxy and old binary — cost if wrong: a restart regression still needs rollback.
- Final: Ruling: Coordinator fresh full Rust/Node/shell checks satisfy remaining suite gate — reviewer ran only focused tests — cost if wrong: these checks are not independently repeated in a second context.
- Final: Ruling: Accept bounded offline replay with measured real-data elapsed time, no arbitrary capacity claim — raw crossing query scans one pool's retained rows, not a new runtime query — cost if wrong: unusually large retained history can slow offline research.
- Final: Ruling: Efficacy, threshold calibration, source freshness and statistical independence remain unverified; retain runtime baseline — short proxy cohorts cannot justify promotion — cost: no immediate demonstrated live win-rate gain.
- Final: Ruling: Verify post-review fixes with real RED→GREEN and complete suite rather than another reviewer — follows the single consolidated fix pass — cost: author-owned verification has shared blind spots.

## Cloud delivery

Delivered source: `ad0e60f1c34ed9590efa146af2436c616872affa`, independently matched local HEAD/GitHub/cloud source. Server built locked release with one job in45.62s. New Linux binary passed init/strict replay on a private empty staged database before activation, including empty B comparison and exact original A/B/C keys. One incorrect expected-SHA argument was caught before build/activation; the corrected verified SHA passed the guard. A startup curl connect-refused was retried successfully. No rollback was needed.

Installed binary, build output and running process SHA256 all equal `e6cb7a3e63bc2b379ebcc901f591e45515ea94c8ed2ee6f1726608b818dec752`. Old binary remains at `/home/ubuntu/token-meme-monitor/.private/signal-quality-20261024T025529Z/tmm.previous`. Post-activation environment/unit/proxy-config SHA256 values matched pre-activation values. The source was pulled through the existing Git path; local credentials/proxy subscriptions were not synchronized. Production database was not replaced. Restore the retained binary and restart `tmm.service` if subsequent old-API/service regressions require rollback; retain additive V2 tables for existing cleanup.

Post-activation healthy marker:2026-10-24T02:55:34Z; final probe02:57:57.116383Z, **143.116seconds** after the marker. This is a short operational check for an offline-only change, not the previous release's30-minute observation and not a long-term capacity claim. All16 scheduled API checks returned200, mode remained shadow. Service PID357972 and proxy PID210877 were unchanged across all probes, NRestarts0; no panic/error-level/shadow-failure logs in this window. Final journal had233info/2warn, both warnings `whale_proxy_failed` (an existing provider path).119 shadow observations and8 market-cycle-completed events were logged; they are observations, not unique trade opportunities. Proxy service was not restarted.

| UTC probe | Tokens ms | Status ms | Overview ms | V2 ms | Shadow observations | Warnings | App MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-10-24T02:56:20.286428+00:00 | 35.51 | 10.95 | 6.09 | 1.64 | 28 | 0 | 222.6 |
| 2026-10-24T02:56:34.132923+00:00 | 44.97 | 11.79 | 6.08 | 1.07 | 45 | 0 | 231.6 |
| 2026-10-24T02:57:19.252216+00:00 | 99.44 | 14.38 | 6.21 | 1.24 | 90 | 2 | 244.6 |
| 2026-10-24T02:57:57.116383+00:00 | 35 | 11.11 | 5.69 | 1.35 | 119 | 2 | 251.7 |

Main APIs and background collection passed the operational gates. Token responses ranged35.0–99.44ms during cache warming; no causal performance improvement is claimed. New strict source background and strategy efficacy still need independently usable evidence; the production decision rules remain unchanged.
