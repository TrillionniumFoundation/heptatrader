# HeptaTrader research and replay

Status: current
Applies to: `research/`, `strategies/`, `scripts/hepta_market_*`, `scripts/hepta_strategy_*`
Last verified commit: moving-main

Research is deterministic and capability-free. A research result can propose a bounded intent; it cannot create a trading session, acquire broker credentials or authorize PAPER/LIVE mutation.

## Canonical manifest

`manifest-v1.json` binds:

- supported mode (`shadow`/replay only);
- strategy definition and implementation entry point;
- required datasets and digest policy;
- time/session semantics;
- feature and decision cadence;
- commission, spread, slippage and delay assumptions;
- validation split policy;
- deterministic output contract;
- explicitly unsupported promotion modes.

The manifest replaces campaign/open/renew/repair/finalizer/attestation ceremony. Provenance is expressed with ordinary immutable input digests and output metadata.

## Reproducible run contract

A run records:

```text
manifest digest
strategy/config digest
input dataset digests
source revision
start/end timestamps
numeric tolerance
output decision-stream digest
metrics and failure reason
```

The same source, manifest and input bytes must reproduce the same decision stream within the declared tolerance.

## Separation from runtime authorization

Research artifacts are untrusted input to a later review/promotion process. They never contain a session token, preview permit, broker account secret or runtime capability. SHADOW output remains read-only even when it contains a syntactically valid TradeIntent draft.

## Validation

See [`../docs/STRATEGY-VALIDATION-PLAN.md`](../docs/STRATEGY-VALIDATION-PLAN.md). The minimum evidence is leakage-free walk-forward/OOS evaluation with explicit costs, capacity and failure-path tests. A profitable backtest alone is not a promotion gate.
