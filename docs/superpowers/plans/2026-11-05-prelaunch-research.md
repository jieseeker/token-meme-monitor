# Prelaunch Research Implementation Plan

**Goal:** research genuine prelaunch potential with point-in-time data and held-out net outcomes.

**Architecture:** one standard-library Python research tool exports bounded cloud history to gzip CSV, then performs local feature and path analysis. Keep production decisions unchanged until evidence passes.

**Tech Stack:** Python sqlite3/csv/gzip/statistics; existing cloud data.

**Spec:** ../specs/2026-11-05-prelaunch-research.md

## Tasks

- [x] Add runnable self-checks for point-in-time pool eligibility, past-only features, delayed entry, stop/target ordering, split purging and event deduplication. Observed missing module and assertion failures before implementation/fix.
- [x] Implement streaming export with daily read connections and historical futures membership.
- [x] Implement fixed features/rules, training selection and locked held-out path evaluation; no external libraries.
- [x] Run on cloud history: 3,681,055 raw rows / 174,716 exported rows / 90 tokens. Retain local input and report, inspect actual rally examples/misses.
- [x] Review evidence and run relevant checks. No rule qualified; reject production promotion. Deliver corrected onset audit and reproducible research rather than an unsupported buy rule. See ../../research/2026-11-05-prelaunch.md.

## Review Focus

Future pool/universe leakage; source gaps and pool switches; small/overlapping samples; cost and stop gap optimism; accidental production promotion without evidence.
