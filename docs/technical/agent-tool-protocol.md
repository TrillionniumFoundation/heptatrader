# Agent tool protocol and client development contract

Status: CURRENT  
Scope: local Agent/MCP/CLI to Tool Gateway; not the separate Execution service protocol  
Implementation: `adapters/mcp/hepta_mcp_server.py`, `HeptaTrade/client/`, `HeptaTrade/tool_host/typed_tool_protocol.cpp`, `HeptaTrade/tools/trading_tool_wire_contract.h`  
Tests: `tests/native_tool_client_tests.cpp`, `tests/unix_tool_server_tests.cpp`, `tests/trading_tool_registry_tests.cpp`, `tests/python/test_installed_runtime_processes.py`

## Transport and request encoding

Each native call opens a local AF_UNIX stream connection. The frame is a four-byte unsigned network-order body length followed by the body. A request body starts with the four ASCII bytes `HTT1`; subsequent fields are a network-order unsigned 16-bit field ID, a network-order unsigned 32-bit byte length, then UTF-8 scalar text. C++ `TypedToolProtocol::EncodeRequest` and Python `encode_request` sort fields by ID. Requests are bounded to 65,536 bytes; the Python adapter also bounds response/MCP message bodies to 1,048,576 bytes. These are limits, not permission to exceed a smaller configured server limit.

The field-ID mapping is intentionally explicit at this cross-language boundary:

| IDs | Fields, in ID order |
|---|---|
| 1–5 | session_token, tool_call_id, tool_name, instrument, order_id |
| 6–10 | symbol, currency, sec_type, exchange, side |
| 11–15 | order_type, quantity, limit_price, reference_price, expires_at_ms |
| 16–20 | timeout_ms, after_sequence, tif, queue_deadline_at_ms, cancel_tool_call_id |
| 21–26 | target_tool_name, protocol_min_version, protocol_max_version, expected_schema_hash, preview_permit, command_id |

Field 2 is the identity of this request/mutation. Field 26 is a *query target* for `execution.get_command_status`, not another identity for a place command. Missing required fields, duplicate/unknown IDs, unsupported protocol ranges, disallowed tool fields, invalid numbers, trailing/malformed fields and excessive lengths are rejected. Per-field limits and semantic rules live in `typed_tool_protocol.cpp` and `TradingToolWireContract`; do not implement a permissive JSON-to-broker bypass.

## Discovery before use

Discovery identifies protocol `hepta.agent-tools`, protocol version 1 and discovery schema version 2. `system.tools.list` returns only the descriptors visible to the authenticated session. Every descriptor includes name, description, required capability, effect, timeout, schema hash, input schema and result schema. Clients verify descriptor hashes and the complete catalog hash, not merely tool names. The MCP process rejects a changed catalog during its lifetime; restart discovery/client state for a deliberately changed catalog.

The registry is the authoritative tool inventory. This table describes caller inputs without duplicating descriptor hashes (which change when schemas change):

| Tool family | Inputs and behavior |
|---|---|
| `system.tools.list`, `account.get_summary`, `portfolio.list_positions`, `orders.list`, `risk.get_limits`, `system.get_health` | No tool arguments. Returned state remains scoped to the session. |
| `system.tools.describe` | `tool_name`; inspect current schema and timeout before constructing calls. |
| `market.get_quote`, `watch.get_snapshot` | `instrument`; validate freshness and completeness in returned payload, not only transport success. |
| `execution.get_command_status` | `command_id`; owner-scoped durable result, including execution epoch/generation. |
| `events.wait` | `after_sequence`, `timeout_ms`; wait timeout is 0–30,000 ms. A timeout is not an execution rejection or fill. |
| `risk.preview_order` | instrument, side, quantity, order_type, tif, expires_at_ms; LMT needs price, contract/reference fields are validated against the session binding. |
| `trade.place_order` | The exact previewed order fields plus `preview_permit`, using the Execution-issued command ID as the request identity. |
| `trade.cancel_order` | Non-negative integer `order_id`; ownership, fencing and order-state checks still apply. Its closed input schema declares `order_id` in `properties` as well as `required`. |
| `risk.preview_flatten`, `trade.flatten_position` | Instrument, then instrument + returned permit. Advertised only when the composition supplies authoritative flatten handlers. Never substitute a client-side position read and arbitrary sell. |
| `system.cancel_request` | `tool_call_id`; cancels this session's pending tool request, not an already-sent venue order. |

## Mutation identity and uncertain outcomes

The safe place sequence is: discover → preview exact order → persist returned `command_id` and permit with the exact order → place once → observe authoritative status/events. A successful preview includes `approved`, `preview_permit`, `command_id`, `permit_expires_at_ms`, `single_use`, service epoch/generation and the authoritative preview. It does not send an order. A permit is single-use, expires, binds order/owner/service identity and cannot authorize a different order or command ID.

For `heptactl`, `--call-id` supplies field 2. For an MCP mutation, the adapter consumes the `arguments.command_id` and uses it as field 2, removing it from native tool arguments. For place, use the ID from the preview, not a newly generated ID. For uncertain retries, preserve that ID and the exact order; query `execution.get_command_status` rather than generating a new identity. A duplicate is an idempotency outcome, not a second send. A transport timeout is not proof of rejection. Even an `ok` place response does not prove a fill: the installed process tests poll authoritative state until the expected fill/cancel settles.

A CLI example against an already provisioned simulator session (never against a Broker endpoint):

```sh
heptactl --socket "$TOOL_SOCKET" --token-file "$TOKEN_FILE" \
  tools describe risk.preview_order
heptactl --socket "$TOOL_SOCKET" --token-file "$TOKEN_FILE" \
  --call-id preview-example-001 call risk.preview_order \
  instrument=EUR.USD symbol=EUR currency=USD sec_type=CASH exchange=IDEALPRO \
  side=BUY order_type=LMT tif=DAY quantity=100 limit_price=1.1002 \
  reference_price=1.1001 expires_at_ms="$EXPIRY_MS"
# Persist the preview response. Use its command_id as --call-id and add its
# preview_permit to the EXACT same fields when calling trade.place_order.
heptactl --socket "$TOOL_SOCKET" --token-file "$TOKEN_FILE" \
  call execution.get_command_status command_id="$PREVIEW_COMMAND_ID"
```

## Results, errors and secret ownership

Responses use a framed JSON envelope with `status`, `tool`, `reason_code`, `detail`, `order_id` and `payload` (possibly null). The tool must match the requested tool. Never reinterpret an unfamiliar status as success. CLI exit codes are 0 success, 2 usage/credential, 3 permission denied, 4 transport/protocol, 5 invalid tool, 6 rejected, 7 duplicate, 8 uncertain, 9 server error and 10 invalid response. MCP preserves the structured envelope and marks statuses other than `ok`/`duplicate` as `isError`.

Tokens are private files, not logs or command-line secret values. A copied token alone does not transfer the server's OS-UID permission. `hepta-sessionctl` is the operator lifecycle client; Agent-facing clients cannot grant themselves sessions. The isolated process fixture deliberately gives a different UID the same disposable token and requires rejection. It then exercises the installed Python MCP bridge and C++ client against the same actual Gateway/Execution daemons, including a real simulator cancel.

## Change and acceptance rules

A change to field IDs, descriptor schemas, statuses or serialization requires C++ codec tests, native discovery tests and the opt-in installed process lane; updating this table or a hash literal is not acceptance. Preserve unknown-field and malformed-response rejection. This reference does not certify the schema of every possible tool payload or remote transport. There is no remote TLS route here, and no client protocol success grants PAPER/LIVE authority.
