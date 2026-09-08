# Tool Gateway and tool registry

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `HeptaTrade/tool_host/`, `HeptaTrade/tools/`  
Tests: `tests/trading_tool_host_tests.cpp`, `tests/trading_tool_registry_tests.cpp`, `tests/unix_tool_server_tests.cpp`

## Responsibilities

The Tool Gateway is the only Agent-facing authority boundary. It authenticates the local peer and session, checks capability and execution-domain bindings, validates the exact tool schema and call semantics, applies gateway-level rate and quantity bounds, records the decision, and forwards permitted work to the Execution Service.

The Gateway must not link a broker adapter, hold a broker credential, connect to a broker API port, or create an alternative order path.

## Public contracts

The registry exposes read and trade tools through versioned descriptors. Each descriptor binds:

- canonical tool name;
- required capability;
- effect (`read` or `trade`);
- timeout;
- canonical input and result schemas;
- descriptor schema hash;
- catalog schema hash.

Wire validation is centralized in `trading_tool_wire_contract.h`. The typed protocol and framing live under `tool_host/`. `system.tools.list` and `system.tools.describe` are the discovery authority; a caller may not infer hidden tools.

## Authorization order

A call is admitted in this order:

1. socket peer identity and framing;
2. session-token lookup;
3. session state, owner, generation, expiry, and recovery-only/fenced state;
4. capability and execution-domain binding;
5. advertised schema hash and field-level validation;
6. bounded rate/quantity policy;
7. tool decision audit;
8. forwarding to the Execution Service.

Passing the Gateway does not mean an order is authorized. Execution applies authoritative quote, position, active-order, kill-switch, persistence, and broker-specific checks again.

## State and persistence

Gateway runtime configuration is immutable after startup. Session state is supplied by the Session Supervisor and is not reconstructed from Agent assertions. Tool decision audit records the normalized request identity and decision without recording secrets.

The Gateway maintains bounded worker and request state only. Execution command truth remains in the Execution Service journal.

## Concurrency

The Unix server accepts bounded concurrent clients and delegates work to workers. Shared session, registry, event, and audit state must be synchronized without holding Gateway locks across broker or unbounded external calls. Request cancellation is advisory for in-flight reads; it must not erase a durable mutation whose outcome is uncertain.

## Failure semantics

Malformed framing, unknown fields, schema mismatch, expired sessions, missing capabilities, invalid quantities, rate exhaustion, unavailable Execution transport, and ambiguous forwarding all fail closed. A forwarding timeout after a mutation may produce `uncertain`; it must not be converted to `rejected` or retried with a fresh command identity.

## Observability

Required dimensions are tool, effect, decision status, reason code, execution domain, and bounded latency. Do not label a call successful until the complete result envelope has been encoded. Track rejected, permission-denied, duplicate, uncertain, timeout, and transport-error outcomes independently.

## Test expectations

Tests must cover every registered tool, required/forbidden fields, descriptor and catalog hashing, peer/session/capability failures, recovery-only and fenced sessions, rate limits, frame bounds, result encoding, cancellation, and Execution transport loss.

## Known limitations

The current implementation is intentionally local-host and single-protocol-version. Remote Agent access, dynamic plugin loading, and broker-specific tools are not authorized. New tools must be represented in discovery and remain independent of direct venue APIs.
