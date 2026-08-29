# HeptaTrader research and replay

Status: current target contract; implementation state is tracked in `docs/development/PLAN.md`
Applies to: `research/`, `strategies/`, current research runner and replay tests
Verification: same-revision CI

Research is deterministic and capability-free. A run may produce a forecast or bounded target-position draft; it cannot create a trading session, acquire broker credentials or authorize PAPER/LIVE mutation.

## Canonical path

```text
RunManifest
-> immutable point-in-time input bytes
-> deterministic normalization/features/strategy
-> append-only EventLog
-> explicit cost/fill evaluation
-> RunSummary
```

The exact object contracts are defined in [`../docs/RESEARCH-PROTOCOL.md`](../docs/RESEARCH-PROTOCOL.md).

## Required run record

```text
run and manifest schema
source revision
strategy/config/implementation digests
dataset URIs and digests
calendar/session/symbol mapping
fold, purge and embargo boundaries
parameters and search budget
commission/spread/slippage/delay/impact assumptions
deterministic seed and numeric tolerance
data-quality counters
decision/event/output digest
metrics, slices and failure reason
```

## Removed current-path concepts

Campaign opener/renewer/repair/finalizer, root custodian, WATCH lease and multi-layer final-audit receipts are not part of the current research protocol. Integrity is expressed by immutable digests, event ordering and compact data-quality/summary fields.

## Capability separation

Research artifacts never contain a session token, broker secret, runtime preview permit or mutation capability. SHADOW output remains read-only even when it contains a syntactically valid target-position draft.

## Validation

A profitable backtest is not a promotion gate. See [`../docs/STRATEGY-VALIDATION-PLAN.md`](../docs/STRATEGY-VALIDATION-PLAN.md) for point-in-time, purged walk-forward, OOS, cost, capacity, regime, failure-path and shadow-parity requirements.
