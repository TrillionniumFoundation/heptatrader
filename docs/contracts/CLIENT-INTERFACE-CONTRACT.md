# Native and MCP Client Interface Contract V1

Status: current core contract
Applies to: `client.native.v1`, `client.mcp.v1`, native callers, CLI composition and the MCP stdio bridge
Verification: `native-client-contracts`, `mcp-contracts`, `tool-catalog-drift`, `protocol-contracts`
Authority: unprivileged client request encoding and response validation; server-side Session, Gateway and Execution remain authoritative

## 1. Boundary and non-authority

The Native and MCP clients are bounded adapters to the local Hepta Tool Gateway. They may encode a request, obtain the session token from an approved source, negotiate the visible tool catalog and validate a returned envelope. They cannot create a session, grant a capability, widen the visible instrument set, approve risk, manufacture a preview permit or convert a transport acknowledgement into Broker truth.

A client call has two distinct outcomes:

1. transport and envelope validation either succeeds or fails locally; and
2. the returned tool status reports the server-side operation outcome.

For the C++ API, `NativeToolClient::Call(...) == true` means a bounded response was received and validated. It does **not** mean `envelope.status == "ok"`. Callers must inspect `status`, `reasonCode`, `orderId` and `payloadJson`.

## 2. Native C++ API

The installed API is defined by [`native_tool_client.h`](../../HeptaTrade/client/native_tool_client.h), [`native_tool_discovery_contract.h`](../../HeptaTrade/client/native_tool_discovery_contract.h), [`typed_tool_protocol.h`](../../HeptaTrade/tool_host/typed_tool_protocol.h) and [`trading_tool_registry.h`](../../HeptaTrade/tools/trading_tool_registry.h).

### Configuration

`NativeToolClientConfig` contains:

| Field | Contract |
|---|---|
| `socketPath` | AF_UNIX stream endpoint. Empty or an overlong `sockaddr_un.sun_path` value is rejected. Endpoint identity and access control are deployment responsibilities of the Session/Gateway boundary. |
| `tokenFile` | Optional path to a mode `0600`, single-link, regular, non-symlink token file owned by root or the current effective UID. When set, it takes precedence over `sessionToken` and is securely reread for each call. |
| `sessionToken` | In-memory alternative when `tokenFile` is empty. It must be non-empty, at most 512 bytes and contain safe UTF-8 text without C0/C1 controls or DEL. |
| `timeoutMs` | One bounded transport deadline per frame operation, inclusive range `1..120000`. |
| `maxResponseBytes` | Inclusive range `1..1048576`; the upper bound is `TradingToolWireLimits::MaximumResultEnvelopeBytes()`. |

The token-file reader validates metadata before opening, opens with `O_NOFOLLOW`, checks descriptor/path identity after the read, requires exactly one hard link and rejects a file whose identity or size changes. Newline terminators are removed before the final token validation. Token contents must never be logged or included in qualification evidence.

### Call identity and discovery

Every request carries a canonical `toolCallId` of 8–128 characters from `[A-Za-z0-9._:-]` and a canonical tool name. Before any non-discovery call, the client obtains `system.tools.list`, validates discovery schema version 2 and retains the catalog digest plus every descriptor digest for that client session.

A non-discovery request is sent only when the requested tool was advertised. The client supplies the descriptor's exact schema hash as `expectedSchemaHash`. A caller-provided hash must match the discovered hash. Discovery list/describe responses are rejected if the catalog changes, a descriptor is substituted, a field is duplicated, a digest is malformed or a requested descriptor does not match the prior list result.

Discovery is authorization-aware: absence from the returned catalog means the tool is not visible to that session, not that the client may construct it manually.

### Results

The native result envelope has exactly these logical fields:

| Field | Meaning |
|---|---|
| `status` | One of `ok`, `permission_denied`, `invalid_tool`, `rejected`, `duplicate`, `uncertain`, `error`. |
| `tool` | Canonical tool name; it must equal the request tool. |
| `reason_code` | Stable reason code, possibly empty only where the server contract permits it. |
| `detail` | Bounded diagnostic text; non-authoritative and unsuitable as a retry decision by itself. |
| `order_id` | Signed integer, `-1` when no order identity is returned. |
| `payload` | A JSON object or `null`; the C++ envelope retains its canonical lexical JSON as `payloadJson`. |

The complete response is bounded to 1 MiB. JSON parsing rejects duplicate fields, invalid UTF-8/control text, non-finite or identity-collapsing numbers, excessive depth/nodes/decoded strings, unexpected envelope fields and unknown statuses.

## 3. MCP stdio bridge

The MCP bridge is [`adapters/mcp/hepta_mcp_server.py`](../../adapters/mcp/hepta_mcp_server.py). It is a newline-delimited JSON-RPC 2.0 stdio process. It is not a network authentication service and does not receive Broker credentials.

### Process configuration

| Environment variable | Contract |
|---|---|
| `HEPTA_TOOL_SOCKET` | Required absolute AF_UNIX path, at most 107 encoded bytes. |
| `HEPTA_TOOL_SESSION_TOKEN_FILE` | Required secure token file consumed by the same trust assumptions as the native client. |
| `HEPTA_TOOL_EXPECTED_UID` | Optional local process-identity assertion; mismatch fails startup/call admission. |
| `HEPTA_MCP_TIMEOUT_SEC` | Integer clamped to `1..120`; default 35 seconds. |

The bridge accepts at most 1 MiB per input line, at most 64 KiB for one native request body and at most 1 MiB for one Gateway response. Oversized input is drained once and rejected; it is never split into multiple requests.

### JSON-RPC methods

| Method | Behavior |
|---|---|
| `initialize` | Returns the requested bounded protocol version, `tools.listChanged=false` and server identity. It grants no tool capability. |
| `ping` | Returns an empty result. |
| `tools/list` | Performs native discovery and projects only the server-visible descriptors into MCP tool descriptions. |
| `tools/call` | Validates `name` and object `arguments`, validates the published descriptor schema, translates registered aliases, then sends one native call. |
| `notifications/*` | Accepted without a response. |
| any other method | JSON-RPC `-32601`. |

Malformed envelopes return `-32600`; invalid method parameters/tool arguments return `-32602`; internal/native failures return a bounded `-32603` message without leaking exception text, filesystem paths, socket details or token material.

`tools/call` returns both a textual canonical JSON envelope and `structuredContent`. `isError` is false for `ok` and for a durable `duplicate` replay; every other status is reported as an MCP tool error. This projection does not change the native status.

### Mutation command identity

For every descriptor whose effect is `trade`, the MCP caller must supply `command_id`. It is removed from the public descriptor arguments only after validation and becomes the native `tool_call_id`.

- Retrying the same mutation after a lost or `uncertain` response reuses the exact command ID and payload.
- `trade.place_order` must use the Execution-issued command ID from the matching preview.
- `intent.apply_target_position` must use the mutation command ID from the matching target-position preview.
- A changed payload requires a new preview and a new command identity; reusing an ID with a different payload is a conflict, not a retry.

After any ambiguous mutation transport failure, the client queries `execution.get_command_status` under the original session/owner identity. It must not generate a replacement command to guess whether the first command reached Execution.

## 4. Compatibility

The native binary protocol is `hepta.agent-tools` version 1 and discovery schema version 2. Supported protocol ranges are explicit. Unknown versions, unknown fields at an authoritative boundary, descriptor digest drift or catalog drift fail closed.

The MCP bridge may expose friendly aliases registered in [`tool-catalog-v1.json`](../../schemas/tool-catalog-v1.json), but aliases are normalized before native encoding and cannot create an additional field or capability. Removing a method, field, status or alias, or changing its authority/idempotency meaning, is a major contract change. Additive optional fields require C++ and Python golden-vector, malformed-input and cross-language parity tests.

## 5. Verification and operations

Required verification includes native request/result round trips, secure token-file negatives, descriptor substitution and catalog-drift negatives, C++/Python wire parity, malformed/oversized JSON-RPC input, control-character redaction, stable mutation retries and tool-name/result mismatches.

Operators must provision the socket and token file through the Session/Gateway deployment policy, rotate/revoke the session through the Session authority and remove token material from process environments and evidence. Client health or successful discovery is not Execution readiness, Broker connectivity or PAPER qualification.
