## Why

The first p4 calibration pass proved that local outcomes are usable only when their labels are clean. Rows built from local snapshots or rows whose signal price diverges from GeckoTerminal by more than 10 percent can bias empirical calibration. Separately, DexScreener pairs that are not indexed yet can be retried too often, and the current SQLite database is large enough that backend health needs a fast built-in report.

## What Changes

- Filter empirical calibration inputs to eligible GeckoTerminal outcomes only.
- Ignore partial horizon labels when deciding whether a row can calibrate that horizon.
- Add a longer retry backoff for DexScreener pairs with no indexed snapshot.
- Add a `health-report` CLI command for database size, row counts, pair freshness, seed status, prediction status, and outcome-quality status.
- Document the latest local rebuild and walk-forward verification.

## Capabilities

### New Capabilities

- `backend-health-calibration`: backend maintenance reports and calibration gates.

### Modified Capabilities

- Existing prediction calibration now treats outcome quality metadata as a hard eligibility gate.
- Existing pair refresh retry scheduling now persists metadata for not-yet-indexed DexScreener pairs.

## Impact

- Affects p4 calibration, prediction rebuild output, worker retry cadence, CLI maintenance workflows, and backend documentation.
- No destructive migration; new behavior is additive and backward-compatible for existing rows.
