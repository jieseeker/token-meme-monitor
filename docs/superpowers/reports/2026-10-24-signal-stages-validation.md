# Signal stages: replay and local validation — 2026-10-24

Deterministic collected-input replay is implemented. Both historical datasets contain only legacy `{}` metadata, so strict acceptance is **zero**. Exploratory proxy replay produced local A/B/C = **33/0/0**, cloud baseline A/B/C = **92/21/1**. All outputs retain unverified source time and **zero opportunity-eligible events**. These results do not establish strategy profitability or exact production execution parity.

## Reproducible experiment

- Engine: the unchanged production `signal_v2::advance` / `tick`; individual strict rows use the same `signal_v2_store::decode` parser.
- Composite experiment identity: `signal_v2_shadow_v1` **and** `min_volume_h1_usd=15000.0`, `focus_score_threshold=65`. These are replay settings; no historical configuration labels are claimed.
- Replay parameter boundary: finite positive min volume and integer focus threshold >=1; nonpositive focus thresholds are rejected before database access. This restriction is specific to offline replay and does not change legacy configuration or shared runtime settings validation.
- Command: `MONITOR_DATABASE_PATH=/absolute/path/consistent-copy.db ./rust-dashboard/target/release/tmm signal-v2-replay`; add only `--legacy-proxy` for the separate exploratory run.
- SQLite opens READ_ONLY before application startup or directory creation. A read transaction keeps both scans consistent. Replay does not call schema initialization, persistence, `observe`, providers, or current `pairs.active` / universe queries. Stdout is one JSON object; errors use stderr and nonzero exit.

## Data actually available

Consistent copies were made with SQLite backup from READ_ONLY connections. Local source: `data/monitor.db`; cloud baseline: the supplied consistent `tmm-signal-v2-cloud-baseline-20261024.db`. No production connection was made by this task.

| Dataset | UTC range | Hours | Rows | Tokens | Stored response groups | Strict accepted |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| local | 2026-10-21T23:16:36.045143Z – 2026-10-23T17:59:18.052866Z | 42.7117 | 190,513 | 376 | 112,491 | 0 |
| cloud | 2026-10-22T09:00:27.599928798Z – 2026-10-23T23:14:15.266993Z | 38.2299 | 312,723 | 373 | 245,068 | 0 |

Neither source supplies seven days. Strict exclusions: local 190,513/190,513; cloud 312,723/312,723. Strict A/B/C and all forward denominators are zero, with return values null. Absence of historical strict input is a finding, not validated absence of market opportunities.

## Exact replay method and bounds

1. Scan stored timestamps to select `[max(earliest, latest-7d), latest]`. Stream by token and SQLite timestamp order; SQLite rounds to milliseconds, so equal SQLite times are sorted by exact RFC3339 instants before grouping. Group by token, exact instant and request fingerprint. The production engine clock remains integer Unix seconds; subsecond timestamp order is preserved but engine thresholds have its existing second precision.
2. Strict rows require actual v1 provider/time/request/presence metadata. Proxy fallback applies only to empty `{}` metadata; malformed V2 metadata is never promoted. Proxy groups use exact stored token/time as an unverified request identity. Positive legacy volumes are exploratory values; legacy zero h1/m5 becomes unknown, not factual zero.
3. From the first eligible stored response group, freeze the highest finite nonnegative-liquidity pool with positive price; equal liquidity chooses the lowest pool address. Later ranking inversions and missing reference pools never switch that event to a sibling. This uses actually stored groups, without inventing historical selected-pool membership.
4. Load prior matching-pool history once at freeze through the existing pair/time index, then retain only prior positive-price samples within the replay window and 24h. The current breakout never enters its own history. Track raw reference-row counts separately from accepted samples. More than 4096 prior raw rows fails safely/cancels advancement rather than manufacturing truncated coverage; at most 4097 samples/count timestamps are retained. Initial over-budget history aborts replay explicitly.
5. Input sorting holds at most 4096 rows sharing one SQLite millisecond; denser input aborts explicitly. Only one token state, its reference-pool history, this small sort buffer and pending labels through 4h+5m are held in Rust. SQLite may use a temporary sort for chronological grouping. No whole-week, whole-universe Rust snapshot vector is allocated. Peak RSS and production capacity were not measured.
6. At each response use only exactly matching contemporaneous stored legacy decisions. Multiple pools count as one token decision. Missing/malformed decision evidence, or conflicting scores, holds risk and disables the legacy-score entry; volume evidence remains the engine’s separate path. No current config is used to reconstruct historical scores.
7. Record actual A entry, B confirmation and C confirmation transitions independently from data/risk state and from the always-false opportunity flag. Risk BLOCK/HOLD hits remain structural observations, never qualified opportunities. Group-level status/reason counts include repeated states and are not additional events.
8. Future labels are processed separately and never supplied to detection. For each event and 15m/1h/4h horizon, take the first same frozen-pool positive sampled price at or after the target, no later than target+300s, and require its previous valid sample at/before the target with a bracketing interval <=300s. No interpolation, extrapolation, substitute later price, cross-pool price, candle extremes or trade execution is inferred. Labels use recorded samples, which may themselves be stale upstream.
9. Every stage/horizon denominator is all structural stage events. Split it into endpoint-eligible, endpoint-missing and right-censored. An unresolved label is right-censored if the overall replay ends before target+300s; otherwise it is endpoint-missing. Zero eligible labels leave return aggregates null. Means below use only eligible endpoints and are descriptive sampled price changes.

## Exploratory coverage

| Metric | Local proxy | Cloud proxy |
| --- | ---: | ---: |
| Accepted proxy rows | 190,513 | 312,723 |
| Frozen tokens | 375 | 371 |
| Frozen-pool rows | 77,267 | 125,001 |
| Post-freeze groups missing reference | 34,466 | 119,198 |
| Accepted new-evidence groups | 46,351 | 71,953 |
| Duplicate/unchanged groups | 30,916 | 53,048 |
| Raw reference intervals | 76,892 | 124,630 |
| Raw intervals >5m | 29,476 | 91,506 |
| Accepted evidence intervals | 45,976 | 71,582 |
| Accepted intervals >5m | 17,576 | 47,937 |
| Missing/unknown h1 rows (all accepted pools) | 72,999 | 133,385 |
| Missing/unknown m5 rows (all accepted pools) | 132,319 | 228,561 |
| Warming-up reference groups | 49,668 | 40,017 |
| History-overflow groups | 0 | 0 |

Cloud raw frozen-reference gaps >5m are 91,506/124,630 = **73.42%**. Accepted-evidence gaps use a different denominator after content/request deduplication. Local sparse history leaves all A hits warming up; cloud A statuses are 67 current / 25 warming up. Reference absence is not attributable solely to a collection cap: today’s cap is three pools per DEX and six total, so historical groups over three do not prove a cap violation. No historical selected-set identity is recoverable from `{}`.

| Dataset / stage | Structural events | Risk at trigger |
| --- | ---: | --- |
| local / A | 33 | BLOCK 6, HOLD 1, PASS 19, WARN 7 |
| local / B | 0 | none |
| local / C | 0 | none |
| cloud / A | 92 | BLOCK 43, PASS 26, WARN 23 |
| cloud / B | 21 | BLOCK 12, PASS 9 |
| cloud / C | 1 | PASS 1 |

Cloud B includes 12 BLOCK and nine PASS risk statuses. The single cloud proxy C has PASS risk but remains unverified and opportunity-ineligible. Counts are not a comparison at an equal research/alert budget and do not prove useful early detection.

## Forward sampled-price coverage

All rows below are **exploratory proxy**, not fills or profit. Mean is conditional on endpoint coverage. Missingness is substantial and nonrandom.

| Dataset / stage | Horizon | Denominator | Eligible | Missing | Right-censored | Mean sampled change |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| local / A | 15m | 33 | 14 | 19 | 0 | -1.1305% |
| local / A | 1h | 33 | 14 | 19 | 0 | -0.1584% |
| local / A | 4h | 33 | 6 | 24 | 3 | -0.3814% |
| cloud / A | 15m | 92 | 30 | 62 | 0 | 0.2359% |
| cloud / A | 1h | 92 | 28 | 60 | 4 | 0.3961% |
| cloud / A | 4h | 92 | 29 | 51 | 12 | -0.1231% |
| cloud / B | 15m | 21 | 20 | 0 | 1 | 1.2983% |
| cloud / B | 1h | 21 | 20 | 0 | 1 | 1.4760% |
| cloud / B | 4h | 21 | 13 | 0 | 8 | 3.3319% |
| cloud / C | 15m | 1 | 1 | 0 | 0 | 2.1217% |
| cloud / C | 1h | 1 | 1 | 0 | 0 | 0.2808% |
| cloud / C | 4h | 1 | 1 | 0 | 0 | 0.2496% |

Local B/C have zero events and zero denominators; all their returns are null. All strict-mode horizons likewise have denominator zero and null returns. No win rate, profit, fill, slippage, peak capture, fee-adjusted return or continuous path outcome was evaluated.

## Completeness and operational parity limitation

The output explicitly sets `frame_completeness_verified=false` and `runtime_execution_trace_reproduced=false`. Sharing the production decoder/engine proves their rules are reused; it does not prove the replay received the exact inputs applied by production.

Worker pool writes commit separately. A failed sibling write can leave individually valid snapshot/signal survivors although runtime skips the whole response. V1 metadata has no expected membership/completion record, so replay may admit those survivors, including a different reference freeze. All-written responses can also be skipped because retirement fails, shadow mode is disabled, the global cooldown is active, or observe/save fails. Production recovery cancels pending evidence on unseen reference writes; collected-input replay cannot reconstruct those operational dispositions. No completeness manifest or runtime execution journal was added. New provenance metadata cannot retrospectively certify these facts.

The four cases remain identity/coverage diagnostics only: local ticker/name clues exist for IRYS, BTR and JCT; no local clue for BIRB. Exact chain/address identities, case event times, historical configs and OI mapping remain unverified. Cloud case identity was not independently established here. No claim that the four rising examples were validated follows from matching ticker text.

## Actual verification

- TDD: initial replay stub compiled, then five assertions failed for missing behavior; implementation passed. Future suffix and liquidity inversion test confirms the earlier nonzero B remains at the same literal confirmation time. Strict legacy exclusion, unknown proxy zeros, read-only schema/count preservation, zero denominators, censored/missing endpoint labels and CLI validation are covered.
- A delayed-liquidity history fixture initially lost distinct content after removing liquidity; corrected hourly h1 values preserve independent evidence. A try/finally mutation deleting initial history then failed the B expectation (exit 101), restored implementation passed. An unsupported SQLite leap-second test failed with a None unwrap, then passed after timestamp rejection was moved before window use. Final focused replay suite: **9/9 passed**.
- Full `cargo test --manifest-path rust-dashboard/Cargo.toml`: **114 passed, zero failed/ignored**, including all 105 prior tests. `node rust-dashboard/static/detail.test.cjs`: exit 0. `bash deploy-cloud.test.sh`: exit 0.
- `cargo build --release --locked --manifest-path rust-dashboard/Cargo.toml`: success. Three release CLI error cases passed: missing database, unknown option, repeated option; stdout empty, stderr error, nonzero exit, and no missing parent directory created.
- Strict and proxy each ran twice on both copies: **byte-identical JSON**. For both copies, complete sqlite_master schema digest, every table row count and full database SHA256 are identical before/after. Four aggregate JSONs and integrity evidence are kept in ignored SDD scratch, without address dumps in this report.
- Local elapsed measurements for two deterministic runs together: local strict 2.815s / proxy 3.994s; cloud strict 3.649s / proxy 8.806s. Optional `/usr/bin/time -l` failed its sandboxed sysctl query; no RSS result is claimed.
- **Live-port smoke blocked/deferred to Task 4:** dashboard-only release on an empty temporary DB, separate port 18531, failed local bind with `Operation not permitted`; scoped escalation did not execute. Legacy/shadow HTTP smoke is not claimed passed. Controller will run temporary-DB/separate-port legacy+shadow smoke on the permitted deployment host before production activation. Existing Rust route/mode tests passed.
- UI unchanged; browser not run. No provider fetch, push, deployment, exact runtime-parity reproduction, four-asset verification or production-load benchmark was performed by Task 3.

Controller owns scoped review, whole-branch review and remaining live API acceptance before rollout.

## Delivery decisions and remaining acceptance gates

- User delegated design, testing/replay, review, commit/push, deployment and rollback; routine step confirmations are covered. This convention is recorded in AGENTS.md.
- Native managed worktree creation succeeded but sandbox writes were denied. Development used `codex/signal-stages` in the original checkout; the host-protected managed checkout was not forcibly removed. This reduces filesystem isolation, so commits and ownership were kept explicit.
- First delivery enables shadow computation and read-only diagnostics. Legacy list, scores and scheduling remain authoritative. Visible B/C promotion waits for trustworthy input and outcome evidence; this release does not yet improve the public opportunity list.
- Three initially Minor Task1 findings were treated as acceptance gaps because missing-data consistency and future-input isolation affect diagnostic trust; they were fixed and re-reviewed rather than deferred.
- Local HTTP startup smoke was blocked by sandbox binding. Equivalent legacy/shadow staged Linux tests against temporary databases and ports18531/18532 must pass on cloud **before** active binary/environment replacement. This directly tests the target host but leaves macOS live startup unverified.
- Task2's accumulated counters were deferred: stage/data/gap/warmup are structured per-observation log fields, countable through journal aggregation. Whole-branch review still triages this observability limit.
- The exact-SHA rollout preserves the existing unit and proxy configuration, retains binary/environment rollback copies, and observes production for30minutes. A failed startup gate aborts before activation; a later old-API/service regression triggers rollback without replacing the production database.

Whole-branch review, fresh final suite, permitted-host startup smoke and production observation are still pending at this report revision. Deployment results will record the deployed source SHA separately from later report-only commits.

## Task 3 scoped-review fix: missing score at the threshold boundary

The review found that substituting score 0 for unavailable/conflicting history could create A when a replay focus threshold was zero or negative. Replay now rejects nonpositive focus thresholds at its own boundary; shared runtime settings validation, legacy configuration, engine formulas and volume-based entry remain unchanged. All `settings_valid` callers were inspected before choosing this replay-only restriction.

TDD RED: `cargo test --manifest-path rust-dashboard/Cargo.toml replay_rejects_nonpositive_focus_thresholds` failed with `accepted unsupported threshold 0` (exit101). After the one-line boundary guard, the focused replay suite passes **11/11**. Temporary SQLite checks cover zero/negative/minimum-integer rejection, missing and conflicting scores at the lowest supported threshold1 producing zero A without volume background, a genuine stored score1 producing A, and missing scores still allowing A from sufficient independent volume history. The previous default-threshold replay aggregates are unaffected; the four database runs and 114-test full-suite evidence above describe the original implementation pass. Controller will run fresh whole-branch checks after review fixes.

The first 11-test parallel run after the guard exposed temporary test database filename collisions (8 passed / 3 failed: malformed-metadata count, UNIQUE snapshot constraint and missing table). Test filenames now include a process-local atomic suffix. The subsequent normal parallel focused run passed all 11; this fixture correction changes no production persistence.
