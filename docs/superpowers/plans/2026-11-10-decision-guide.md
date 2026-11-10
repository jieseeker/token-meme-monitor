# Candidate Decision Guide Implementation Plan

> **For agentic workers** Use superpowers:executing-plans task by task. User authorized implementation, review, push and cloud delivery in this chat.

**Goal** Make current candidate eligibility, next evidence and expiry understandable beside honestly scoped historical evidence.

**Architecture** Reuse existing frontend state and API responses. Replace the compact top summary, cache the prelaunch summary and formal shadow summary during the existing refresh, render evidence only for its own eligible cohort.

**Tech Stack** Existing plain JavaScript/CSS, Node assert/vm, Rust include_str dashboard.

**Spec** ../specs/2026-11-10-decision-guide.md

## Global Constraints

All existing strong gates apply, A window 300 seconds, $100 <=0.30%, $1K <=0.50%, risk_off blocks. B is structural observation. Normal list only Binance futures Alpha. Seven-day same-pool research prices remain distinct from net simulation. No dependencies. Existing user untracked monitoring directory remains intact. Reuse current checkout on feature branch per host workflow.

## Review Focus

Stale/future evidence, initial blocked event later PASS, missing/negative statistics, B promoted by copy, refresh caching or expiry hiding new states.

### Task 1 — Decision explanation and comparable evidence

- [ ] Extend detail.test.cjs with decisionGuideHTML cases covering live observation and deadlines, risk/market/price/slippage blocks, future/stale sources, frozen blocked origin, cohort isolation, sample counts and negative net median. Run node rust-dashboard/static/detail.test.cjs and observe missing-function RED.
- [ ] Implement decisionGuideHTML(token), reuse the gate helpers, display truthful current and initial reasons. Add prelaunchSummary and confirmationSummary to existing refresh. Include the rendered guide in the detail cache key. Replace trade-summary and its obsolete assertions with current decision assertions. Run Node regression to GREEN.
- [ ] Inspect related callers and commit scoped implementation. Expected Node exit 0 and git diff --check exit 0.

### Task 2 — Render, review and release

- [ ] Verify live-data local preview at 1280, 390 and 320 widths, including long risk explanations and history scrolling. Run Impeccable detector once and repair findings. Expected no overflow or inaccessible state.
- [ ] Run Node page suite, cargo test in rust-dashboard, bash regression and git diff --check. Expected all pass. Obtain independent whole-branch review and resolve material findings through regression.
- [ ] Merge/push the verified feature and run existing cloud deployment. Confirm source version, HTML byte equality, eight services and real desktop/narrow rendering. Record actual outstanding validation and snapshot results here.
