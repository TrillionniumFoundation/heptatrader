# Authoritative state and decision snapshot contract

Status: current target contract; implementation state is tracked in `development/PLAN.md`
Applies to: `HeptaTrade/state/`, `HeptaTrade/execution/`, `HeptaTrade/intent/`, `HeptaTrade/tool_host/`
Verification: same-revision CI

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
