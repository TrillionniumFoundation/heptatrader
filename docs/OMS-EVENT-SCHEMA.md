# OMS journal event schema v4

Status: CURRENT  
Applies to: `OmsJournal::kSchemaVersion == 4`  
Implementation: `HeptaTrade/oms_journal.h`, `HeptaTrade/oms_journal.cpp`  
Tests: `tests/oms_journal_durability_tests.cpp`, `tests/oms_journal_schema_v4_tests.cpp`

## Purpose

The OMS journal is the durable mutation and broker-callback evidence stream used for command idempotency, restart recovery, owner fencing, venue correlation, and incident reconstruction. It is append-only JSON Lines: one complete JSON object per line.

Application logs, strategy state, CSV snapshots, and SHADOW receipts do not replace this journal.

## Current record

The v4 writer emits all keys below. Optional strings are emitted as empty strings and optional numeric values as zero when no evidence exists.

```json
{
  "schema_version": 4,
  "event": "broker_execution",
  "ts_ms": 1800000000000,
  "order_id": 101,
  "client_req_id": "cmd-001",
  "instrument": "EUR.USD",
  "side": "BUY",
  "qty": 1.0,
  "price": 1.125,
  "status": "Filled",
  "reason": "",
  "source": "ib.execDetails",
  "trace_id": "session-001",
  "req_id": "cmd-001",
  "risk_code": "",
  "venue": "IB",
  "strategy": "",
  "account": "DU000000",
  "event_id": "event-001",
  "execution_domain": "PAPER",
  "request_hash": "sha256:...",
  "venue_correlation_id": "hepta:cmd-001",
  "broker_callback_type": "execDetails",
  "broker_service_epoch": "ib-service-epoch-001",
  "broker_connection_epoch": 7,
  "broker_request_id": 55,
  "broker_error_code": 0,
  "broker_message": "",
  "broker_advanced_order_reject_json": "",
  "broker_why_held": "",
  "broker_execution_id": "0001.0002.0003",
  "broker_remaining_quantity": 0.0,
  "broker_market_cap_price": 0.0
}
```

## Field contract

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | Writer schema; current value is 4. |
| `event` | string | Typed lifecycle, control, projection, or broker-evidence event. |
| `ts_ms` | integer | UTC Unix epoch milliseconds. |
| `order_id` | integer | Service/broker-correlated order ID; `-1` when not applicable. |
| `client_req_id` | string | Legacy alias retained for compatibility. |
| `req_id` | string | Stable command/request ID used by current code. |
| `trace_id` | string | Session or trace identity. |
| `event_id` | string | Event-level deduplication identity when present. |
| `request_hash` | string | Canonical normalized mutation hash when applicable. |
| `venue_correlation_id` | string | Stable service-owned venue correlation. |
| `venue`, `account`, `execution_domain` | strings | Authority scope. |
| `strategy`, `instrument`, `side` | strings | Business attribution and normalized intent. |
| `qty`, `price` | finite numbers | Event quantity and price semantics defined by event type. |
| `status`, `reason`, `risk_code`, `source` | strings | Outcome, typed failure, and producer attribution. |
| `broker_callback_type` | string | Original callback family such as `orderStatus`, `error`, or `execDetails`. |
| `broker_service_epoch` | string | Broker-owning service lifetime identity. |
| `broker_connection_epoch` | unsigned 64-bit integer | Venue connection generation, from 0 through 18446744073709551615. |
| `broker_request_id` | integer | Venue callback request ID where applicable. |
| `broker_error_code`, `broker_message` | integer/string | Broker diagnostic evidence. |
| `broker_advanced_order_reject_json` | string | Broker-supplied advanced reject payload retained as evidence text. |
| `broker_why_held` | string | Broker hold explanation. |
| `broker_execution_id` | string | Stable venue execution identity. |
| `broker_remaining_quantity` | finite number | Broker-reported remaining quantity. |
| `broker_market_cap_price` | finite number | Broker-reported market-cap price when present. |

Known numeric fields must be finite and fit their destination types. Integer fields require integer JSON tokens: fractional or exponent notation is rejected, including `1.0` and `1e4`. Overflow and nonzero floating-point values that underflow to zero are rejected. Missing historical fields retain their defaults; a present field with an invalid type or value never receives a default.

JSON syntax must be complete. Only top-level fields populate the event; nested extension fields cannot supply or override event identity. Duplicate object keys are rejected at every depth after JSON escape decoding. Strings decode all JSON escapes and valid Unicode surrogate pairs into UTF-8; invalid UTF-8 and unpaired surrogates are rejected. The writer preserves control characters with JSON escapes and serializes finite doubles with round-trip precision using the locale-independent JSON decimal point.

## Event families

### Mutation lifecycle

- `order_intent`
- `place_send_attempt`
- `place_sent`
- `place_activated` (two-phase simulator activation receipt)
- `place_outcome_uncertain`
- `cancel_send_attempt`
- `cancel`
- `reject`
- `risk_blocked`
- `flatten_intent`
- `flatten_send_attempt`
- `flatten_sent`
- `flatten_noop`
- `flatten_reject`
- `flatten_outcome_uncertain`

### Command, projection, and ownership

- `execution_command_resolved`
- `cancel_command_resolved`
- `execution_projection_failed`
- `execution_projection_resolved`
- `session_owner_fenced`
- `session_owner_fence_release`
- `order_owner_reconciled_terminal`

### Broker evidence

- `broker_order_accepted`
- `broker_order_status`
- `broker_error`
- `broker_execution`
- `broker_completed_order`
- `broker_completed_orders_end`
- `broker_execution_details_end`

Producers may add read-only diagnostic event types, but consumers must not infer a mutation or terminal economic fill from an unknown event. A filled status without the required execution evidence is not sufficient for economic reconciliation.

## Durability and idempotency

Risk-increasing mutations follow this order:

1. normalize and bind owner/session/domain;
2. bind stable command ID and canonical request hash;
3. append and durably commit intent;
4. append and durably commit the send attempt;
5. call the venue;
6. append the observed sent, rejected, callback, or uncertain result.

Critical mutation, owner-fence, projection, and broker-evidence events are synchronously durable when the configured critical-sync policy is active. A write, path-identity, or synchronization failure poisons the writer and cannot be reported as success.

A command ID is the mutation idempotency key. `event_id` is an event deduplication key. When historical records have no `event_id`, compatibility replay may use a bounded fingerprint, but that fingerprint is not a substitute for command identity.

## Replay and compatibility

The current writer emits v4. The parser retains missing-field defaults for historical records and preserves the raw line for audit. It validates the complete journal before invoking any consumer callback; a malformed later record causes replay to fail without publishing an earlier partial projection. Higher-level recovery code must use only fields it understands and must not promote an unresolved send attempt to rejection or success.

The lightweight `OmsRecover` helper is retained for legacy v1/v2-style projections. Canonical Execution recovery uses the richer command journal, venue correlations, broker callback evidence, connection epochs, and authoritative barriers.

Schema changes must be additive or have an explicit migration. Every new field or event requires:

- writer/parser round-trip coverage;
- old-fixture replay coverage;
- restart tests at affected durable boundaries;
- module and protocol documentation updates;
- qualification updates when broker evidence semantics change.

## Verification

Run the C++ durability and v4 round-trip tests through:

```bash
./scripts/dev_core.sh
```

For offline inspection of a JSONL journal:

```bash
python3 scripts/verify_oms_journal_replay.py \
  --journal runtime-logs/oms_journal.jsonl \
  --minimum-schema 1
```
