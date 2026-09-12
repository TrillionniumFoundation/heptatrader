# Session supervisor

Status: CURRENT
Applies to: repository HEAD
Implementation: `HeptaTrade/tool_host/session_supervisor_protocol.cpp`, `HeptaTrade/tool_host/session_supervisor_lease_store.cpp`, `HeptaTrade/tool_host/unix_session_supervisor_server.cpp`, `HeptaTrade/cli/hepta_sessionctl.cpp`
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

Writes must use a durable temporary file, file `fsync`, atomic rename, and directory `fsync` where supported. A partially committed generation may not be guessed from the token file.

## Operator interface

`hepta-sessionctl` provisions, inspects, revokes, and performs terminal cleanup through the supervisor protocol. It must be invoked by a deployment-controlled operator identity. An Agent cannot mint, rotate, or revoke its own authority.

The repository intentionally does not provide an automatic PAPER campaign opener or automatic kill-switch disarm path.

## Concurrency and fencing

Each mutation of a lease is serialized against the durable generation. Repeated commands are idempotent by their command identity. A lower or stale generation is rejected. Expiry, revoke, or ambiguous cleanup must cause the Gateway and Execution Service to reject new risk before external side effects are possible.

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
