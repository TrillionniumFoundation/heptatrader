# Execution Wire Contract V1

Status: current core contract
Applies to: `hepta.execution-wire.v1`, Gateway-to-Execution request transport and local typed Tool Gateway framing
Verification: `protocol-contracts`, `gateway-boundary`, `negative-paths`, `idempotency`, `tool-catalog-drift`
Authority: structural encoding and compatibility only; Execution remains the sole mutation, OMS, permit and reconciliation authority

## 1. Scope and authority

This contract defines the bounded local transport used to carry a validated Agent/Gateway request toward the server-side authority boundary. A structurally valid message does not prove the issuer, session, capability, risk decision, permit freshness or Broker outcome. Gateway and Execution must independently validate their own authoritative inputs before state admission.

The registry summary is [`execution-wire-v1.json`](../../schemas/execution-wire-v1.json). The C++ implementation is split across [`typed_tool_protocol.cpp`](../../HeptaTrade/tool_host/typed_tool_protocol.cpp), [`typed_tool_framing.cpp`](../../HeptaTrade/tool_host/typed_tool_framing.cpp), [`typed_tool_result_codec.cpp`](../../HeptaTrade/tool_host/typed_tool_result_codec.cpp) and the shared semantic boundary in [`trading_tool_wire_contract.h`](../../HeptaTrade/tools/trading_tool_wire_contract.h).

## 2. Outer frame

Each AF_UNIX message is one frame:

```text
uint32 body_length_be
body[body_length]
```

`body_length` is unsigned network byte order, non-zero and bounded by the caller-selected ceiling. Requests are at most 65,536 bytes; result envelopes are at most 1,048,576 bytes. Reads and writes use one monotonic deadline for the entire header/body operation. Timeout, early close, zero length, oversize length and partial transfer are typed failures; no partial request is admitted.

The transport is one request and one response per connection. A successful frame write is not a mutation acknowledgement. If the connection fails after submission, the caller treats a mutation outcome as uncertain and queries the authoritative command status using the original command identity.

## 3. Request body

The body begins with the four-byte magic `HTT1`, followed by fields in strictly increasing numeric ID order:

```text
uint16 field_id_be
uint32 value_length_be
value[value_length]
```

Values are non-empty UTF-8 text, contain no NUL and are individually bounded. Duplicate, unknown, out-of-order, empty, malformed or tool-inapplicable fields are rejected. The canonical field IDs are generated from [`tool-catalog-v1.json`](../../schemas/tool-catalog-v1.json):

| ID | Field | Semantics |
|---:|---|---|
| 1 | `session_token` | Bearer possession for a server-side Session binding; not Broker authority. |
| 2 | `tool_call_id` | Canonical stable request/mutation identity. |
| 3 | `tool_name` | Canonical registered tool name. |
| 4 | `instrument` | Server-visible instrument key. |
| 5 | `order_id` | Existing order identity for cancellation. |
| 6–9 | `symbol`, `currency`, `sec_type`, `exchange` | Explicit contract fields where an operator order contract requires them. |
| 10–14 | `side`, `order_type`, `quantity`, `limit_price`, `reference_price` | Order/target numeric and direction inputs under the registered tool schema. |
| 15 | `expires_at_ms` | Absolute expiry bound to the request/permit semantics. |
| 16–17 | `timeout_ms`, `after_sequence` | Event wait bounds/cursor. |
| 18 | `tif` | Explicit time-in-force; current supported profiles require `DAY`. |
| 19 | `queue_deadline_at_ms` | Admission deadline; expiration rejects before work is accepted. |
| 20 | `cancel_tool_call_id` | Owner-scoped request cancellation target. |
| 21 | `target_tool_name` | Discovery describe target. |
| 22–23 | `protocol_min_version`, `protocol_max_version` | Explicit compatibility range including version 1. |
| 24 | `expected_schema_hash` | Descriptor digest obtained from current discovery. |
| 25 | `preview_permit` | Opaque server-issued permit; exact scope and one-time rules belong to Execution/intent contracts. |
| 26 | `command_id` | Command queried by `execution.get_command_status`; not an alternative mutation ID. |

The mandatory envelope fields are session token, tool call ID, tool name and protocol range. Tool-specific fields are accepted only according to the registered descriptor and semantic validator. Required fields are not inferred from defaults.

## 4. Numeric representation

Risk-sensitive numeric text uses the `hepta.numeric.fixed-v1` policy: scale 1,000,000 and maximum absolute raw value 9,000,000,000,000,000. Canonical conversion rejects NaN, infinity, negative zero, excess scale, range loss and a binary64 projection that cannot round-trip to the identical fixed microunit.

Compatibility C++ members may still be `double`, but encoding first obtains canonical fixed decimal text and decoding validates the exact fixed boundary before populating compatibility fields. Quantity, price, risk comparison, identity and digests do not obtain authority from binary64.

## 5. Tool-specific admission

The field allowlist and required-field rules are closed-world. Examples:

- `system.tools.list`, `account.get_summary`, `portfolio.list_positions`, `orders.list`, `risk.get_limits` and `system.get_health` accept no tool-specific fields.
- `market.get_quote`, `watch.get_snapshot`, `decision.get_snapshot` and `risk.preview_flatten` require `instrument`.
- `system.tools.describe` requires `target_tool_name`; `system.cancel_request` requires `cancel_tool_call_id`.
- `execution.get_command_status` requires `command_id`; `trade.cancel_order` requires `order_id`.
- target preview requires instrument, quantity, reference price and expiry; target apply additionally requires its preview permit.
- operator order preview/place requires the descriptor-defined instrument/order fields; place additionally requires its preview permit.
- flatten requires instrument and preview permit.

Unknown tools and fields fail closed. A field allowed by the transport still must pass the descriptor schema, session capability, environment, ownership, freshness, risk and Execution checks.

## 6. Result envelope

The response body is one canonical JSON object with exactly:

```json
{
  "status": "ok|permission_denied|invalid_tool|rejected|duplicate|uncertain|error",
  "tool": "canonical.tool.name",
  "reason_code": "STABLE_REASON_OR_EMPTY_WHERE_ALLOWED",
  "detail": "bounded diagnostic text",
  "order_id": -1,
  "payload": null
}
```

`payload`, when present, must be a JSON object. The decoder rejects extra/missing/duplicate envelope fields, invalid Unicode/control characters, invalid order IDs, non-finite or underflow-collapsing numbers, oversize strings, excessive depth/nodes and unknown statuses.

`duplicate` means an accepted, identical replay where the owning authority supports it; it is not a generic transport success. `uncertain` means the client cannot infer a terminal mutation result and must reconcile/query. Free-form `detail` is diagnostic; retry and safety decisions use status, stable reason code and authoritative state.

## 7. Identity, replay and failure semantics

Mutation admission is bound to the server-side session/owner, current epoch/fence, stable command ID, normalized payload digest, decision/permit/snapshot context and Execution authority. Same owner plus same command ID and same payload returns the registered replay behavior. Same ID with a changed payload is rejected.

A Gateway acknowledgement cannot replace journal-before-send, venue observation or reconciliation. Timeouts and disconnects never authorize a new-risk retry with a new ID. Safe reduction remains subject to its explicit proof and cannot be generalized from an arbitrary failed request.

## 8. Compatibility and verification

Version 1 is selected only when the declared minimum/maximum range includes it. Unknown field IDs are rejected rather than ignored. Reassigning a field ID, changing canonical numeric meaning, weakening a required field, changing replay identity or changing a status is a major version change.

Every change requires byte-level C++/Python golden vectors, encode/decode round trips, duplicate/out-of-order/unknown-field negatives, malformed lengths, partial/timeout transport fixtures, cross-language numeric parity and end-to-end Gateway/Execution negative paths. The schema summary, generator, C++ binding, MCP binding, contract registry and tests change atomically.
