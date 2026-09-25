# Tool Gateway and tool registry

Status: CURRENT
Applies to: repository HEAD
Implementation: `HeptaTrade/tool_host`, `HeptaTrade/tools`
Tests: `tests/trading_tool_host_tests.cpp`, `tests/trading_tool_registry_tests.cpp`, `tests/unix_tool_server_tests.cpp`, `tests/python/test_gateway_observability.py`, `tests/python/test_installed_runtime_processes.py`, `tests/audit_journal_lifecycle_tests.cpp`

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

The HJA2 audit retains synchronous mutation intent/outcome and Supervisor
records. Active-chain caching requires unchanged file identity/metadata **and**
a healthy inode-bound Linux change watch. Same-size writes within one filesystem
timestamp tick invalidate it. Unsupported/failed/overflowed watches use complete
active-chain verification instead of metadata-only trust. Existing full-chain
verification and corruption tests remain unchanged. The supported writers append
under the file lock; this is not isolation against a compromised same-UID process
rewriting through mmap or deliberately ignoring that protocol. Untrusted Agents
must retain separate OS identities and no access to the audit directory. Its default active limit is 1 GiB. Routine observation stops consuming
space at limit minus 32 MiB; new-risk/session-admission records stop at limit
minus 16 MiB. Session provision, renewal and rotation are all admission records;
extending a lease cannot consume the exit reserve. Bound cancellation/flatten
and the explicit Supervisor revoke/recovery/terminal operation set may use that
final reserve, but cannot exceed the absolute limit. Unknown Supervisor
operations are rejected before appending; new enum members do not inherit safety
capacity implicitly. Classification
comes from the trusted registry and peer/session binding, not client input.
Unknown mutation records are conservatively admission-class, never observations.

At the observation threshold only routine audit records are shed: the ordinary
read result is preserved, no durable receipt is invented, and the process-local
`audit_log.observations_shed` counter increases. Mutation persistence failures
retain rejection-before-dispatch or uncertainty-after-dispatch semantics. The
Gateway observation also reports active bytes, limit, reserve and known state.
The existing OMS/Gateway reporter validates these optional fields, emits fixed
Prometheus capacity/shed counters, and reports unknown capacity, maintenance,
admission pause and shed-observation alerts. Old producers report absence, not
healthy zero. This source-side collection is not proof of external alert delivery.
Disk failure still fails closed; the reserve is not a disk-space reservation or
permission to skip authorization. An abusive valid exit caller can consume a
finite reserve, so it is not an unlimited emergency-service guarantee.

Stopped-state `hepta-sessionctl --audit-seal-stopped /absolute/audit-path` archives
the complete current file under its SHA-256 in `audit-path.segments/`, synchronizes
the archive and directories, then atomically installs an HJA3 predecessor anchor.
`--audit-verify /absolute/audit-path` verifies every retained predecessor and
returns cumulative HJA2 record count. Each segment retains its own local sequence;
the digest/count anchor binds the complete preceding history. No record expires.
The operator must stop Gateway first, seal as the audit-file owner, verify, and
restart. Old open writers fail path-identity validation after the replacement.
An active empty anchor makes a retry idempotent but still syncs its directory.

Failure before active replacement leaves the old file authoritative. Failure
of directory sync after replacement is reported as publication-indeterminate;
retry/verify the same state, never restore an older audit. Startup rejects a
missing or corrupt referenced segment and a missing/empty active file beside
retained segments. Legacy-only bytes can be sealed with zero HJA2 records; zero
is not permission to discard those bytes. Initial open also synchronizes the
parent directory before reporting readiness. Crash leftovers are not automatic deletion candidates. Backup/restore
must preserve the active file and the entire `.segments` directory together.
Older binaries that merely treat HJA3 as legacy text are **not** compatible
verifiers after segmentation. There is no automatic online rotation or pruning.
Full history verification grows with retained bytes; active append size is bounded,
not long-horizon startup I/O.


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

## Field-level development reference

The shared [`Agent tool protocol`](../technical/agent-tool-protocol.md) gives the
actual framing and field map, discovery/schema hashing and request/result
examples. `system.tools.list` is the authoritative complete catalog; avoid
maintaining a second handwritten copy of every tool schema in this document.
HTT1 numeric tags and field names come from `typed_tool_fields.def`; C++ uses
that table directly and `render_protocol_reference.py --write-tool-fields`
regenerates the MCP map. The Python profile's excluded fields remain excluded.
Required/forbidden-field and business validation still belong to each boundary.
Change the registry and positive/negative protocol vectors together when changing
a wire contract; generated mechanical agreement does not prove compatibility. Renaming a private function does not require a
new source-token gate.

## Measured gateway runtime

See [Gateway runtime observations](../technical/gateway-runtime-observability.md)
for actual producers, lock boundaries, fixed result bins, latency scopes and
installed reporting. Application results and local socket-write outcomes are
counted independently; neither grants trading authority.
