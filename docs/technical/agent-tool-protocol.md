# Agent, MCP and Tool Gateway development contract

Status: CURRENT
Applies to: local Agent entry and Tool Gateway protocol v1

## Implementation map

| Responsibility | Owning implementation |
|---|---|
| MCP JSON-RPC process, discovery and stable mutation retry | `adapters/mcp/hepta_mcp_server.py` |
| Native C++ client | `HeptaTrade/client/` |
| Tool descriptors and input/result schemas | `HeptaTrade/tools/trading_tool_registry.cpp`, `trading_tool_wire_contract.h` |
| Native request codec and result JSON | `HeptaTrade/tool_host/typed_tool_protocol.cpp`, `typed_tool_result_codec.cpp` |
| Length framing and timeout handling | `HeptaTrade/tool_host/typed_tool_framing.cpp` |
| Session/capability policy and dispatch | `HeptaTrade/tool_host/trading_tool_host.cpp`, `tool_gateway_session_policy.cpp` |

The registry's `system.tools.list` / `system.tools.describe` responses are the
field-schema authority. No Agent field can supply Broker account identity,
credentials, final risk state or an execution-domain override.

## Byte-level transport

Each AF_UNIX stream request is a four-byte unsigned big-endian body length,
followed by `HTT1` and ascending field-ID TLVs. A TLV contains a big-endian
uint16 ID, a big-endian uint32 byte count, and UTF-8 scalar text. Body length is
at most 65,536 bytes; one encoded field is at most 32,768 bytes. Responses use
the same outer length framing and a UTF-8 JSON envelope, bounded to 1,048,576
bytes by the MCP adapter. Framing reads must tolerate fragmentation and reject
early close; one `recv` is not assumed to contain a frame.

| IDs | Meaning |
|---|---|
| 1, 2, 3 | session token, tool-call identity, tool name |
| 4, 5 | instrument, order ID |
| 6–11 | symbol, currency, security type, exchange, side, order type |
| 12–16 | quantity, limit price, reference price, expiry milliseconds, timeout milliseconds |
| 17–21 | after-sequence cursor, TIF, queue deadline, cancelled call ID, described tool name |
| 22, 23, 24 | protocol minimum, maximum, expected descriptor schema hash |
| 25, 26 | preview permit, command ID field |

These are transport IDs, not a promise that every tool accepts every field.
`system.tools.describe` maps its `tool_name` argument to field 21;
`system.cancel_request` maps `tool_call_id` to field 20. Reserved transport
identity fields cannot be injected through ordinary tool arguments.

## Discovery and schema binding

Discovery uses protocol `hepta.agent-tools`, selected protocol version 1, and
schema version 2. Versions must be integers, not strings, booleans or truncated
floats. A descriptor contains exactly name, description, required capability,
effect, timeout, input schema, result schema and schema hash.

The descriptor hash covers UTF-8 NUL-separated name, description, capability,
effect, timeout and canonical input/result schema JSON. The current schema
serializer preserves the emitted object-key order; arbitrary JSON key sorting
is not an interchangeable encoding. The catalog hash covers sorted
`name=schema_hash\n` entries. Hash mismatch, duplicate descriptor and catalog
change within one MCP process lifetime are failures, not hot upgrades.

## Mutation example and uncertainty

For an already provisioned, permitted session, an MCP cancel request has this
shape (the numbers/identity are an example, not authority to cancel an order):

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"trade.cancel_order","arguments":{"order_id":42,"command_id":"command-0001"}}}
```

The adapter removes the added MCP `command_id` argument and uses its exact value
as the native tool-call identity, field 2. A retry after `uncertain` reuses the
same ID and request content. For `trade.place_order`, the ID must instead match
the Execution-issued ID from the corresponding risk preview. Never create a
new mutation identity merely because an MCP response was lost.

The native result requires string `status`, `tool`, `reason_code`, `detail`, an
integer (not boolean) `order_id`, and `payload`. Supported statuses are `ok`,
`permission_denied`, `invalid_tool`, `rejected`, `duplicate`, `uncertain` and
`error`. MCP reports `ok` and `duplicate` as non-error results, preserving the
actual envelope. Mismatched tool, unknown status, malformed UTF-8/JSON,
duplicate keys and non-finite or underflowed numbers are rejected.

## Identity, bounds and process lifecycle

`HEPTA_TOOL_SOCKET` must be an absolute Unix path of at most 107 encoded bytes.
`HEPTA_TOOL_SESSION_TOKEN_FILE` must be a regular single-link file owned by the
current identity or root, with no group/world access. The leaf is opened
nonblocking/no-follow and checked for stable inode/metadata before and after
reading. A regular-to-FIFO substitution cannot block before validation.
`HEPTA_TOOL_EXPECTED_UID`, when set, must match the bridge identity. Tokens
never appear in result/error logs. Deployment owns the parent-directory trust
boundary; no remote transport is introduced.

Each native call opens a fresh connection. `HEPTA_MCP_TIMEOUT_SEC` defaults to
35 and is bounded to 1–120 seconds. An oversized stdin line is drained once,
produces one failure, and does not consume the following valid line. Process
restart intentionally discards the descriptor cache; execution state remains in
Execution, not in the MCP process.

## Executable references

`tests/python/test_mcp_bridge.py` exercises the real subprocess and Unix stream,
fragmented responses, repeated uncertain/duplicate mutation identity, discovery
hashes, malformed values, token links and line bounds. Its synthetic gateway
is a protocol fixture, not a Broker. The native client, registry and Unix server
C++ tests independently exercise the opposite side and authority checks.
