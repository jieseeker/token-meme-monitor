## Data definitions

| Metric | Definition | Notes |
| --- | --- | --- |
| Structure stage | Current V2 structure stage from `/api/structure`. | The summary uses the same state mapping as the structure card. |
| Current action | `structureState().note`. | Describes the next observable event, not a trade recommendation. |
| Trigger | Stage-specific condition that would advance the structure. | Intended to make the stage auditable. |
| Invalidation | Stage-specific condition that would reject or reset the structure. | Intended to make the stage auditable. |
| 1h turnover | `volume_h1 / liquidity_usd`. | Shown as a percentage; `--` when liquidity is zero or missing. |
| 1h large-trade net flow | Sum of deduplicated buy USD minus sell USD for trades in the last hour. | Uses the existing large-trade endpoint; the detail list remains a 24-hour sample. |
| Transfer USD value | `transfer amount × current token price`. | Approximate because the transfer and price snapshots may have slightly different timestamps. |

## Frontend behavior

The first detail card renders a summary bar above its metric grid. The bar is derived from existing data and does not add a network request. When large-trade data is not yet available, the net-flow metric shows `--` and updates in place after the existing whale request finishes. Cross-pool duplicate transactions are deduplicated by transaction hash before net flow is calculated.

## Watchlist

The watchlist is local to the browser and persists in `localStorage` under `tmm-watchlist-v1`. The detail card exposes a single toggle, and the token list can filter to watched tokens only. The watchlist does not affect scoring, refresh priority, or backend requests.
