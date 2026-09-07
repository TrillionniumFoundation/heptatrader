# Agent entry and MCP bridge

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `adapters/mcp/hepta_mcp_server.py`, `HeptaTrade/cli/`, `HeptaTrade/client/`  
Tests: `tests/native_tool_client_tests.cpp`, `tests/unix_tool_server_tests.cpp`

## Responsibilities

The Agent entry layer exposes the bounded HeptaTrader tool catalog to an Agent, CLI caller, or native client. It discovers tool descriptors, validates the advertised protocol and schema hashes, encodes typed requests, reads bounded responses, and preserves caller-generated mutation identities across uncertain retries.

It is not an execution authority. It does not own broker credentials, broker sockets, order state, final risk decisions, or reconciliation truth.

## Public contracts

- MCP process: `adapters/mcp/hepta_mcp_server.py`.
- CLI: `heptactl` for ordinary tool calls and `hepta-sessionctl` for operator-owned session lifecycle operations.
- Native library: `hepta_native_tool_client`.
- Transport: authenticated local Unix stream socket with bounded length-prefixed messages.
- Discovery: protocol `hepta.agent-tools`, current protocol version 1 and discovery schema version 2.
- Mutations require a stable canonical `command_id`; an uncertain retry reuses the exact same value.

The entry layer rejects unknown fields, unsupported protocol ranges, duplicate tool descriptors, invalid schema hashes, oversized messages, malformed result envelopes, and result/tool mismatches.

## Identity and secret handling

The MCP bridge reads its session token from a regular, non-symlink file. It checks owner, mode, link count, size, stable metadata before and after open, UTF-8 validity, and the configured Agent UID. The token is sent only over the local tool socket and is never logged.

Deployment must assign each mutually untrusted Agent a separate OS identity, token, socket, and trust-domain configuration. Sharing one token or Unix identity across Agents collapses the owner and fencing model.

## State and concurrency

The Python MCP bridge keeps only a discovered descriptor catalog and catalog digest in memory. Each native call uses a new Unix socket connection and a bounded timeout. A catalog digest change during one MCP process lifetime is rejected rather than silently accepted.

The native client performs the equivalent wire and discovery checks in C++. Neither client persists execution state.

## Failure semantics

- Missing or unsafe token: fail before connecting.
- Missing, relative, or oversized socket path: fail before connecting.
- Timeout or early close: return an error/uncertain outcome; never manufacture success.
- Unknown result status or malformed JSON: fail closed.
- Lost mutation response: query command status or retry with the same command ID; never generate a new mutation identity for the same intent.

## Observability

Caller-visible output must include tool name, typed status, reason code, detail, order ID when available, and payload. Logs must omit session-token contents. Connection, protocol, validation, timeout, and response-envelope failures should be counted separately by the hosting environment.

## Test expectations

Tests cover discovery, framing, schema-hash validation, result parsing, command-ID requirements, Unix server interaction, timeout bounds, and malformed responses. Changes to field IDs, protocol versions, result statuses, or command-ID rules require cross-language compatibility vectors.

## Known limitations

The entry layer currently supports a local Unix transport only. It does not provide remote TLS, multi-host routing, or LIVE authority. Those capabilities must be introduced as separate trust domains rather than by exposing the broker API directly to an Agent.
