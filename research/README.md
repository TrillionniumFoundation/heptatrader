# HeptaTrader research and replay

Status: current target contract; implementation state is tracked in `docs/development/PLAN.md`
Applies to: `research/`, `strategies/`, current research runner and replay tests
Verification: `canonical-full-suite` on the exact revision

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
static strategy source paths and raw-byte SHA-256 digests
runner support path and raw-byte SHA-256 digest
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

## Deterministic runner

The checked-in static contract and fixture are verified with:

```bash
python3 research/run_protocol.py verify --manifest research/manifest-v1.json
```

The runtime install carries this capability-free runner, its
`protocol_support.py` helper and the static contract, but intentionally omits
the experimental strategy source files that the static manifest names.  From
an installed tree, `self-test` and full
RunManifest replay remain supported; static verification must point at the
source checkout with `--root` and fails closed when those assets are absent:

```bash
/usr/local/share/heptatrader/research/run_protocol.py self-test
/usr/local/share/heptatrader/research/run_protocol.py verify \
  --manifest /usr/local/share/heptatrader/research/manifest-v1.json \
  --root /path/to/heptatrader
```

A replay consumes a RunManifest plus JSON arrays of quote and target records:

```bash
python3 research/run_protocol.py run \
  --manifest run-manifest.json \
  --quotes quotes.json \
  --targets targets.json \
  --output run-summary.json
```

`replay` is an alias for `run`. Inputs are normalized with exact decimal
arithmetic, per-instrument point-in-time ordering, duplicate-timestamp checks
and bounded multi-instrument quote books. Independent instrument books may be
interleaved; malformed or capability-bearing input fails closed with a stable
`reason_code` and a non-zero exit status. A valid
RunSummary includes immutable input/manifest/event/output digests, data-quality
counters, explicit cost/capacity assumptions, exposure/drawdown/tail-loss
metrics, time/regime slices and concentration fields. None of these outputs is
a session token, preview permit or promotion grant.

Cost assumptions are applied deterministically: observed bid/ask is the base
price; `spread_bps` adds conservative adverse fill bps, `fee_bps` charges each
fill's notional, and annualized `borrow_bps`/`funding_bps` accrue on marked
short/gross exposure over a fixed 365-day year. These amounts appear in
`explicit_cost`/`holding_cost` and PnL rather than being metadata-only. A
concrete RunManifest must also provide an explicit final OOS interval after
the walk-forward test folds (`final_out_of_sample.start_ms` and `.end_ms`).

CLI JSON documents are UTF-8, duplicate-key-free and bounded to 16 MiB with a
maximum nesting depth of 128; malformed or oversized documents fail closed.
