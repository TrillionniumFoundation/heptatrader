# Research and replay protocol

Status: current target contract; implementation state is tracked in `development/PLAN.md`
Applies to: `research/`, `strategies/`, Python research package and replay tests
Verification: same-revision CI

## Minimal durable objects

The current research path has exactly three canonical objects:

1. **RunManifest** — immutable identity of code, data, parameters, costs, folds and numeric policy.
2. **EventLog** — append-only normalized observations, decisions and simulated execution events.
3. **RunSummary** — data quality, metrics, failures, digests and validation result.

Campaign open/renew/repair/finalizer, root-custodian and multi-layer receipt chains are not part of the current path.

## RunManifest minimum fields

```text
schema/run_id/mode/source_revision
strategy id/version/definition/implementation digests
dataset URIs and SHA-256 digests
UTC calendar/session/symbol mapping
feature and label horizons
fold boundaries, purge and embargo
parameters and parameter-search budget
commission/spread/slippage/delay/impact assumptions
numeric tolerance and deterministic seed
output locations and unsupported promotion modes
```

## EventLog

Events use canonical ordering and stable identifiers. Changed data at the same authoritative timestamp is rejected. Missing, stale, duplicate and out-of-order counts are data-quality fields, not workflow states.

## RunSummary

A summary reports net performance, drawdown, turnover, trade count, exposure, tail loss, cost share, capacity, time-in-market, regime/time slices, worst slice, concentration, deterministic output digest and exact failure reason.

## Reproducibility

The same source bytes, manifest, dataset bytes and seed must reproduce the same decision stream within the declared tolerance. A refactor requires a golden parity fixture.

## Capability boundary

Research output is untrusted input to a later reviewed runtime change. It never carries broker credentials, session tokens, preview permits, PAPER/LIVE capability or automatic promotion authority.
