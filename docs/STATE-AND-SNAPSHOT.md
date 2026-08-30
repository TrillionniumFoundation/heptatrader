# Authoritative state and decision snapshot contract

Status: current Simulator/core implementation contract; external PAPER projection remains separately validated
Applies to: `HeptaTrade/state/`, `HeptaTrade/execution/`, `HeptaTrade/intent/`, `HeptaTrade/tool_host/`
Verification: `canonical-full-suite` on the exact revision; target-host checks are separate

## Authority

Execution Service is the only authority that may combine venue/account/order/position/risk state into a decision snapshot. Gateway and Agent may request a snapshot but cannot supply, replace or widen authoritative fields.

## Typed snapshot

A snapshot contains one identity and one monotonic generation envelope:

```text
execution_service_epoch
fencing_generation
state_generation
collection_watermark
event_watermark
captured_at_ms
fresh_until_ms
```

Payloads include health, normalized quote, account, positions, active/recent orders, risk limits and current risk usage. Components are typed internal values; JSON is serialization after validation, not the parsing boundary.

## Consistency rules

A snapshot is authoritative only when:

- epoch and fencing generation remain unchanged across capture;
- state generation is stable across all component reads;
- no event watermark change invalidates the collection;
- required account, order, position and quote projections are complete;
- the quote is positive, ordered, instrument-bound and fresh;
- timestamps and watermarks are monotonic;
- payload and response size remain bounded.

Any failed rule returns a stable `DECISION_SNAPSHOT_*` reason and no partially authoritative object.

## Concurrency semantics

A fill, cancel, broker correction, reconnect or service restart during collection either appears wholly in the captured generation or causes rejection. Retrying obtains a new snapshot; callers never merge components from separate snapshots.

## Permit binding

A target-position preview permit binds at least:

```text
owner/session/account/execution-domain
instrument and normalized target
snapshot epoch/fence/generation/watermarks
current position and quote identity
derived plan and deterministic risk-policy version
expiry and command fingerprint
```

Apply revalidates the binding at Execution authority and atomically consumes or replays it.

The core contract fixtures cover stale/incomplete snapshots, epoch/fence/
generation mismatches and permit binding. The Simulator path is the only
implemented venue path in this contract; IB PAPER host certification remains an
external acceptance activity.
