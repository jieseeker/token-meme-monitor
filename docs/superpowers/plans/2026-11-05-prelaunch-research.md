# Prelaunch Research Implementation Plan

**Goal:** research genuine prelaunch potential with point-in-time data and held-out net outcomes.

**Architecture:** one standard-library Python research tool exports bounded cloud history to gzip CSV, then performs local feature and path analysis. Keep production decisions unchanged until evidence passes.

**Tech Stack:** Python sqlite3/csv/gzip/statistics; existing cloud data.

**Spec:** ../specs/2026-11-05-prelaunch-research.md

## Tasks

- [ ] Add runnable self-checks for point-in-time pool eligibility, past-only features, delayed entry, stop/target ordering, split purging and event deduplication. Observe failures before implementing.
- [ ] Implement streaming export with daily read connections and historical futures membership.
- [ ] Implement fixed features/rules, training selection and locked held-out path evaluation; no external libraries.
- [ ] Run on cloud history, retain local reproducible report and inspect actual rally examples/misses.
- [ ] Review evidence, run relevant checks, document limits and implement only the justified discovery improvement.

## Review Focus

Future pool/universe leakage; source gaps and pool switches; small/overlapping samples; cost and stop gap optimism; accidental production promotion without evidence.
