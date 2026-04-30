## Context

The project has enough backend intelligence that the main bottleneck is review ergonomics. Operators need to understand why a token was highlighted, what p4 predicted, what actually happened, and whether the case should inform future strategy work. This should be a dashboard workflow, not a separate notebook.

## Goals / Non-Goals

**Goals:**

- Provide one case detail view that joins the main decision artifacts.
- Make review queues fast to scan and filter.
- Allow lightweight local notes or watchlist state.
- Reuse existing Streamlit and backend query patterns.

**Non-Goals:**

- Rebuild the dashboard in another frontend framework.
- Add collaborative multi-user review.
- Add trading execution or wallet integration.
- Make dashboard writes part of prediction or alert scoring.

## Decisions

- Case detail data will come from backend query helpers, not ad hoc SQL embedded in UI blocks.
  - Rationale: query behavior needs unit tests and should stay reusable for reports.

- Review queues will be derived from existing predictions, outcomes, health findings, and scheduled report artifacts.
  - Rationale: queues should reflect real workflow states without creating another classification system.

- Notes and watchlist state will be local and optional.
  - Rationale: review state is useful, but it should not block read-only dashboard usage.

- Exports will use the same filtered case model shown in the UI.
  - Rationale: exported data should match what the operator reviewed.

## Risks / Trade-offs

- Joining too much data can slow Streamlit rendering. Query helpers should paginate or limit queues.
- Notes introduce dashboard writes; they must be isolated from prediction and outcome tables.
- Dense UI can become hard to scan. The first implementation should prioritize tables, filters, and compact detail sections over decorative layout.
