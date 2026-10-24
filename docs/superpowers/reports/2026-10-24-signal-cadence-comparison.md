# V2 Evidence-Gap Candidate Comparison

Date: 2026-10-24. This is a same-input, read-only replay comparison; it is not a production hit-rate study or a trading backtest.

## Input

- Snapshot: `/private/tmp/tmm-signal-cadence-20261024-0440.db`
- SHA256: `a128b3d27c289148658d9b8021e2aabe44e696c6a579854dab1fbc33fb872091`
- Rows: 361,924 snapshots from 2026-10-22 09:00:27 UTC through 2026-10-24 04:40:10 UTC.
- Settings: focus score 65, minimum 1-hour volume $15,000.
- Candidate: `signal_v2_shadow_v2`, evidence gap limit 480 seconds.
- Baseline: `signal_v2_shadow_v1`, evidence gap limit 300 seconds.

Both modes were run twice against the immutable snapshot. Outputs were byte-identical, and the input SHA256 remained unchanged after replay.

## Results

Strict mode, which uses only source-time-verified rows, was unchanged: A/B/C = 14/0/0 for both versions.

Legacy-proxy mode changed as follows:

| Metric | Baseline 300s | Candidate 480s |
| --- | ---: | ---: |
| A events | 103 | 105 |
| B events | 28 | 30 |
| C events | 1 | 1 |
| B risk | 10 PASS / 1 WARN / 17 BLOCK | 11 PASS / 2 WARN / 17 BLOCK |
| Recent-volume PASS B | 6 | 6 |
| All-B 4h mean return | 1.35% | 0.91% |
| PASS-B 4h mean return | 0.50% | -0.71% |
| Recent-volume PASS-B 4h mean | 1.57% | -0.40% |

The all-B cohort also lost endpoint coverage: 15-minute endpoints went from 0 missing to 4 missing, and 1-hour endpoints from 0 missing to 3 missing. The candidate's earlier B confirmations do not always have usable nearby future observations.

## Case Review

- **IRYS:** unchanged; no B in either version.
- **FOREST:** unchanged; B remains risk BLOCK, with 52.04% sampled 4-hour return.
- **AKE:** unchanged; B remains risk BLOCK, with -1.96% sampled 4-hour return.
- **IN:** baseline produced a PASS/recent-volume B at 12:51:35 UTC with 12.73% sampled 4-hour return. The candidate replaced it with an earlier WARN B at 10:57:53 UTC with 1.00% sampled 4-hour return.
- **HAKIMI:** new PASS B at 08:31:54 UTC, with -1.03% sampled 4-hour return.
- **X:** new PASS/recent-volume B at 09:46:38 UTC, with 0.94% sampled 4-hour return.

## Decision

**No-go.** The 480-second candidate is reverted.

The only trustworthy strict-history result is unchanged. In the exploratory proxy, the candidate adds two PASS B events but also replaces IN's existing PASS B with an earlier WARN B. The added PASS B cases have small or negative 4-hour returns, while the PASS-B and recent-volume PASS-B cohort means both deteriorate. Endpoint coverage also worsens.

This result does not justify changing the production 300-second continuity rule. It also does not prove that 300 seconds is optimal; it only shows that this particular 480-second candidate lacks positive evidence in the current snapshot. Continue collecting strict-history data before another threshold change.
