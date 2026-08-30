# Agent decision snapshot and target-position contract

Status: current Simulator/core implementation contract; external PAPER integration remains experimental
Applies to: `HeptaTrade/intent/`, `HeptaTrade/tools/`, `HeptaTrade/tool_host/`, `HeptaTrade/execution/`, `adapters/mcp/`
Verification: `canonical-full-suite` on the exact revision; target-host checks are separate

## 1. Boundary

An ordinary Agent expresses a desired portfolio state and execution bounds. It does not choose authoritative current position, account value, broker order ID, final quantity, venue route, reference price or risk result.

Ordinary mutation path:

```text
decision.get_snapshot
intent.preview_target_position
intent.apply_target_position
```

Raw `trade.place_order` is operator-only and must be absent from ordinary Agent deployment profiles and examples.

## 2. `decision.get_snapshot`

Input:

```json
{"instrument":"EUR.USD"}
```

The result is one bounded object from one Execution-owned state generation. It includes owner scope, instrument, execution epoch, fencing generation, state/event/collection watermarks, capture/freshness timestamps, quote, account, positions, active/recent orders, risk limits/usage and health.

A change in epoch, fence, generation or invalidating event during collection rejects the request. `authoritative=true` is emitted only after all consistency checks pass.
The quote's `observed_at_ms` is also bound to the collection window
(`collection_started_at_ms <= observed_at_ms <= collection_completed_at_ms`).
An apply revalidation may reuse the exact attested window only when the
component fingerprint is unchanged; a changed fingerprint starts a new
generation and cannot borrow an older timestamp floor.

## 3. `intent.preview_target_position`

Input:

```text
{
  "instrument": "EUR.USD",
  "target_position": 1.0,
  "max_slippage_bps": 5.0,
  "expires_at_ms": <now_ms + 30000>
}
```

`expires_at_ms` is a caller-supplied absolute UTC epoch in milliseconds and
must be strictly in the future (within the bounded intent lifetime).  The
angle-bracket expression is a schema-shaped placeholder only; a real request
must replace it with an integer `now_ms + 1..60000` before encoding.

Trusted code computes:

```text
delta = target_position - authoritative_current_position
```

It derives side, quantity, order shape and authoritative valuation; applies portfolio netting/budget and deterministic risk; and returns either a typed no-op, rejection or bounded plan with an opaque preview permit.

Agent input can narrow bounds but cannot provide current position, quote, account, risk usage, generation or final plan.

## 4. Preview permit

The permit binds:

- owner/session/account/execution domain;
- normalized target and bounds;
- execution epoch, fence and state/event watermarks;
- authoritative current position and quote identity;
- derived execution plan;
- portfolio/risk-policy versions;
- expiry and command fingerprint.

The permit is stored by Execution authority, not reconstructed from untrusted client JSON. It is single-use across command IDs and replayable only by the exact accepted command. The preview response includes an Execution-issued `mutation_command_id`; the apply call's transport `command_id` must copy that value exactly. This server-issued binding is intentional: the underlying Execution preview permit is already bound to that command ID.

## 5. `intent.apply_target_position`

Input contains the same normalized intent, preview permit and the exact `mutation_command_id` returned by the matching preview (carried as the apply call's stable `command_id`). Execution atomically performs:

```text
lookup permit
-> validate owner/expiry/unconsumed state
-> revalidate epoch/fence/generation/intent/plan
-> rerun current deterministic safety gates
-> persist command and permit consumption
-> send or return durable replay
```

Apply fails closed on permit expiry/absence, cross-command reuse, input mismatch, generation change, risk rejection, journal failure or unavailable venue authority.

## 6. Idempotency

| Case | Result |
|---|---|
| same command ID + same normalized request after accepted apply | durable duplicate/replay result |
| same command ID + changed request | idempotency conflict |
| different command ID + already consumed permit | permit-consumed rejection |
| expired/stale generation permit | typed stale/expired rejection |
| no-op target | typed no-op; no zero-quantity venue command |

## 7. Capability profiles

Recommended ordinary Agent capabilities:

```text
system.read market.read account.read portfolio.read orders.read risk.read
intent.apply trade.cancel trade.flatten events.read
```

`decision.get_snapshot` and `intent.preview_target_position` use the ordinary
`system.read`/`risk.read` capabilities shown above; the names of tools are not
capability strings.  `trade.place_order` and `risk.preview_order` remain in the
separate `operator.*` profile.

Operator raw-order authority uses a separate profile and preferably a separate local socket/identity. WATCH is read-only. LIVE remains unsupported.

## 8. Stable reason families

```text
DECISION_SNAPSHOT_*
INTENT_POLICY_INVALID
INTENT_TARGET_LIMIT
INTENT_NO_CHANGE
INTENT_PLAN_READY
INTENT_PREVIEW_EXPIRED
INTENT_PREVIEW_NOT_FOUND
INTENT_PREVIEW_CONSUMED
INTENT_PREVIEW_MISMATCH
INTENT_GENERATION_CHANGED
INTENT_RISK_REJECTED
INTENT_JOURNAL_FAILED
INTENT_UNSUPPORTED_ENVIRONMENT
```
