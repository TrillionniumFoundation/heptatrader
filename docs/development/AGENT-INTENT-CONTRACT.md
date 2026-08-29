# Agent decision snapshot and target-position contract

Status: current target contract; implementation status is tracked in `PLAN.md`
Applies to: `HeptaTrade/tools/`, `HeptaTrade/tool_host/`, `HeptaTrade/execution/`, `adapters/mcp/`
Last verified commit: moving-main

## 1. Boundary

An ordinary Agent expresses a desired portfolio state; it does not choose authoritative account state, current position, broker order ID, final quantity, venue route or risk result.

The ordinary mutation path is:

```text
decision.get_snapshot
intent.preview_target_position
intent.apply_target_position
```

Raw `trade.place_order` is an operator/professional-strategy interface and must not be exposed in ordinary Agent deployment profiles.

## 2. `decision.get_snapshot`

Input:

```json
{"instrument":"EUR.USD"}
```

The result is a single bounded object assembled by one Execution authority. It includes:

```json
{
  "authoritative": true,
  "instrument": "EUR.USD",
  "execution_service_epoch": "...",
  "fencing_generation": 7,
  "collection_watermark": 100,
  "event_watermark": 98,
  "snapshot_watermark": 100,
  "collection_started_at_ms": 0,
  "collection_completed_at_ms": 0,
  "quote": {},
  "account": {},
  "positions": [],
  "orders": [],
  "risk_limits": {},
  "health": {}
}
```

The service captures epoch/generation/watermark values before and after collection. Any change that makes the object internally inconsistent rejects the request. `authoritative=true` is emitted only after those checks.

## 3. `intent.preview_target_position`

Input fields:

| Field | Rule |
|---|---|
| `instrument` | server-bound canonical instrument |
| `target_position` | finite signed target in instrument units |
| `max_slippage_bps` | finite, non-negative and no wider than session policy |
| `expires_at_ms` | bounded future deadline |

The trusted service computes:

```text
delta = target_position - authoritative_current_position
```

It then derives side, absolute quantity, order shape and valuation from its snapshot. Portfolio netting and deterministic risk run in trusted code. A no-op target returns a typed no-op preview, not a zero-quantity order.

Preview output includes an opaque permit binding:

- normalized intent and target;
- current authoritative position;
- execution epoch and fencing generation;
- quote/account/position/snapshot generations;
- risk-policy version;
- derived execution plan;
- expiry and command fingerprint.

## 4. `intent.apply_target_position`

Input contains the same normalized intent, the preview permit and a stable caller-generated command ID. Execution atomically consumes or replays the permit.

Apply fails closed when:

- the permit is expired, unknown or already consumed by another command;
- intent fields differ from the preview;
- epoch, fencing, quote, account or position generation changed;
- the target would violate current deterministic risk;
- the derived reduction would cross zero under reduce-only policy;
- journal persistence or venue authority is unavailable.

A same-command retry returns the durable result. A changed request under the same command ID returns an idempotency conflict.

## 5. Agent-visible versus authoritative fields

Agent-visible inputs are goals and bounds. These are never accepted from Agent input as authoritative facts:

```text
current position, account equity, margin, active orders,
reference/valuation price, quote timestamp/generation,
execution epoch/fence, broker order ID, final risk decision
```

## 6. Capability model

Recommended ordinary Agent capabilities:

```text
system.read market.read account.read portfolio.read orders.read risk.read
decision.snapshot intent.preview intent.apply trade.cancel trade.flatten events.read
```

`operator.trade.place` is separate and absent from ordinary Agent environment examples. WATCH sessions have no mutation capability. LIVE environments remain unsupported.

## 7. Result reason codes

At minimum:

```text
INTENT_NO_CHANGE
INTENT_TARGET_INVALID
INTENT_TARGET_LIMIT
INTENT_SNAPSHOT_STALE
INTENT_GENERATION_CHANGED
INTENT_PREVIEW_EXPIRED
INTENT_PREVIEW_MISMATCH
INTENT_RISK_REJECTED
INTENT_JOURNAL_FAILED
INTENT_UNSUPPORTED_ENVIRONMENT
```

Reason codes are stable machine contracts; free-form detail is diagnostic only.
