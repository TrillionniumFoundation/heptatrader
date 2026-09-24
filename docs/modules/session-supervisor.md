# Session supervisor

Status: CURRENT
Applies to: repository HEAD
Implementation: `HeptaTrade/tool_host/session_supervisor_protocol.cpp`, `HeptaTrade/tool_host/session_supervisor_lease_store.cpp`, `HeptaTrade/tool_host/unix_session_supervisor_server.cpp`, `HeptaTrade/cli/hepta_sessionctl.cpp`, `HeptaTrade/tool_host/session_supervisor_internal.h`, `HeptaTrade/tool_host/session_supervisor_support.cpp`, `HeptaTrade/tool_host/session_supervisor_terminal.cpp`, `HeptaTrade/tool_host/session_supervisor_lease_codec_internal.h`, `HeptaTrade/tool_host/session_supervisor_lease_codec.cpp`
Tests: `tests/unix_session_supervisor_server_tests.cpp`, `tests/session_supervisor_lease_store_migration_tests.cpp`

## Responsibilities

The Session Supervisor owns durable Agent session leases and their generation, expiry, capability, execution-domain, recovery, revoke, and terminal-cleanup state. It separates an operator-authorized session from an arbitrary token presented by an Agent.

It does not own broker credentials or decide order risk. It coordinates fencing and recovery with the Gateway and Execution Service.

## Logical state machine

The implementation maps its durable records to the following logical states:

```text
PROVISIONED -> ACTIVE -> EXPIRING -> REVOKING -> CLOSED
                  |          |           |
                  +-> RECOVERY_ONLY <- FENCED
                               |
                         TERMINALIZING
                               |
                             CLOSED

Any ambiguous durable or cross-service result -> UNCERTAIN / fail closed
```

- **PROVISIONED:** durable identity and capabilities exist; token publication may not yet be complete.
- **ACTIVE:** exact owner, generation, token, capability, and expiry agree.
- **RECOVERY_ONLY:** no risk increase; status, reconciliation, cancel, and guarded exit operations only.
- **FENCED:** prior owner/generation cannot add risk.
- **TERMINALIZING:** one-way recovery and terminal evidence are being committed.
- **CLOSED:** token and authority are revoked and cleanup is durable.

## Persistent contract

The lease store is authoritative for session metadata. Records bind Agent identity, session ID, generation, capability set, execution domain, expiry, and recovery/fence information. Store migration must be explicit and crash safe; a newer or malformed format is rejected.

Writes use a durable temporary file, file `fsync`, atomic rename, and directory `fsync`. The complete encrypted envelope is checked against the reader's 2 MiB bound before rename, and ordinary admission leaves bounded space for revoke/fence/PAPER terminal evidence. Once rename has published a new inode, a later directory-sync or post-write verification failure is **indeterminate**, not an ordinary rollback: the store keeps the published in-memory view and refuses further mutations until a fresh owner reopens and validates durable state.

## Operator interface

`hepta-sessionctl` provisions, inspects, revokes, and performs terminal cleanup through the supervisor protocol. It must be invoked by a deployment-controlled operator identity. An Agent cannot mint, rotate, or revoke its own authority.

The repository intentionally does not provide an automatic PAPER campaign opener or automatic kill-switch disarm path.

## Concurrency and fencing

Each mutation of a lease is serialized against the durable generation. Repeated commands are idempotent by their command identity. A lower or stale generation is rejected. Expiry, revoke, or ambiguous cleanup must cause the Gateway and Execution Service to reject new risk before external side effects are possible.

Socket ingress is separated from the mutation serializer. A bounded worker set
reads and authenticates complete frames without holding the operation mutex;
only decoded operations enter the serialized state transition, and the mutex is
released after outcome audit before a potentially slow socket reply. The accept
queue is bounded and overload is closed before any lease operation is invoked.
A slow or partial authorized peer therefore consumes one bounded ingress slot,
not the global lease-state lock.

The supervisor must not hold store locks while waiting indefinitely for an external service. Cross-service work is bounded and represented as explicit intermediate or uncertain state.

## Failure semantics

- Store unreadable, malformed, unsafe, or version-incompatible: fail closed.
- Token/store disagreement: recovery-only or fenced; never choose the more permissive state.
- Execution fencing or reconciliation timeout: preserve the fence and report uncertain.
- Cleanup failure: do not mark the session closed until token, lease, and execution ownership are reconciled.
- Restart during terminalization: replay the durable terminal state; do not reopen authority.

## Observability

Emit structured state transitions with Agent ID, session ID, generation, execution domain, operation, result, reason code, and durable sequence. Never emit token contents. Track active, recovery-only, fenced, expiring, uncertain, and cleanup-required counts.

## Test expectations

Tests cover provisioning, expiry, rotation, migration, stale-generation rejection, duplicate commands, fencing, recovery-only behavior, restart points around atomic writes, Unix peer checks, terminal cleanup, and failed/ambiguous Execution coordination.

## Known limitations

The source tree supplies session primitives, not proof that a deployment has created the required OS users, directories, tokens, or protected operator paths. Those are host controls and remain fail-closed until independently verified.

## Persistent and wire format reference

[`Session lease format`](../technical/session-lease-format.md) separates the
HSS1 wire protocol, encrypted store envelope and versioned plaintext records.
It documents the actual migration restrictions, finalization states and failure
points. Those format rules, not the illustrative logical state diagram above,
are the compatibility contract for restart and rollback.

## Internal implementation boundaries

The public supervisor, serialized lease state and HSS1/HSL formats are unchanged.
`session_supervisor_terminal.cpp` owns recovery/finalization coordination;
`session_supervisor_support.cpp` owns shared receipt/scope helpers;
`session_supervisor_lease_codec.cpp` owns HSL parsing/serialization and the
existing encrypted envelope. The lease store retains physical file identity,
atomic persistence and state-transition ownership. Private helper declarations
are not installed APIs. Existing transition and crash tests exercise the same
method bodies after extraction; no durable boundary is split across owners.

This extraction does not claim that every logical state has been redesigned.
Format consolidation or a new state machine would require separate migration
and recovery evidence, not a line-count target.

## Bounded control work and unrelated owners

Complete-frame operation admission uses the configured Supervisor I/O interval as
one monotonic lock-wait/recovery-observation budget. Queue timeout rejects before
intent or lease mutation. The local deadline is never an HSS1/HEX1 field, permit,
lease extension or cancellation of an already-dispatched authority operation.
Execution identity/event-identity checks, request writes and response reads share
that deadline for recovery observation; a lost/late result remains uncertain.

PAPER recovery reserves its exact `(agent_id, session_id)`, commits the durable
and local recovery-only fence, then releases the Gateway mutation-dispatch lock
while observing Execution. Supervisor serialization is also released for that
reserved owner's recovery call. The same owner and group terminal operations
are refused while it is in flight; unrelated owners may proceed. Before adopting
a response, the host checks the current binding/generation and local deadline.
Exception unwinding restores the Supervisor serializer and releases the owner
reservation. Terminal group commits remain exclusive, not general parallel work.

Ordinary cleanup takes at most 16 ordered lease records per page, resumes with a
fair token cursor, and stops starting another owner after a 100 ms selection
budget. An already-selected recovery owner retains its configured I/O work
interval, rather than starving forever if durable fencing consumes 100 ms.
This is not a claim that the entire pass finishes in 100 ms.
It does not wait behind a control operation: `SUPERVISOR_MAINTENANCE_BUSY` leaves
state untouched for the next pass. Each copied record is re-read before mutation.
Startup restoration shares one 60-second budget across owners rather than a new
60-second retry window for every owner. Existing exact-authority checks remain.

These are cooperative application/transport budgets, **not** a hard real-time
promise for filesystem sync, arbitrary in-process callbacks, scheduler stalls or
exclusive terminal commits. A disk-stalled syscall cannot safely be abandoned by
detaching a writer thread. Such host failures require process-manager containment
and recovery; they must not be reported as a successful clean shutdown.

## Lease history capacity and typed terminal commits

The existing Gateway observation includes an optional, cached `lease_store`
object. It separates unfenced stored leases, fenced/recovery/finalizing records,
permanent acknowledgement groups, plaintext bytes by family, actual encrypted
bytes, canonical rewrite bytes, admission headroom and exit reserve. Counts do
not certify that a lease is currently unexpired or authorized. A published-but-
indeterminate write has `known=false`; reports must not export its byte values as
healthy capacity. Fixed-cardinality persistence timing is process-local only.

Capacity accounting is calculated during the existing serialization/migration,
not by rescanning receipt history on every telemetry tick. Oversized canonical
output rejects before encryption and file I/O. The 2 MiB reader bound, 96 KiB
base exit reserve and 20 KiB per PAPER record reserve are unchanged. A history-
heavy old store can still fence/remove active records, but cannot grow ordinary
admission into its exit reserve. No acknowledgement or retired identity expires.

Operators should observe history growth and remaining headroom before admission
pauses. No automatic deletion/compaction is supplied for acknowledgement history:
an arbitrary TTL would permit retired owner-token reuse. Any future cold-history
store must preserve exact rejection/replay semantics across restart and failure.

`SessionSupervisorTerminalAckRequest` groups finalization and terminal-owner
bindings by name. The store validates all fields against the original receipts;
seven independent owner-field substitutions are regression-tested before commit.
This is an internal C++ interface cleanup, not a change to HSL8, HSS1 or HEX1.
`Put` and `Replace` share mutable-record shape validation; historical decoding
keeps its version-specific restrictions. All prior migration tests remain.
