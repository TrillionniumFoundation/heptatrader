# Execution recovery and reconciliation

Status: CURRENT
Applies to: canonical Execution coordinator, IB terminal snapshots and simulator recovery

## Implementation map and non-goal

| Component | Entry points / responsibility | Behavioral evidence |
|---|---|---|
| `HeptaTrade/execution/execution_coordinator.cpp` | `RecoverFromJournal`, `ResolveUncertainPlaceCommands`, `ResolveUncertainCancelCommands`, `ReconcileOrderOwners` | `tests/execution_coordinator_tests.cpp` |
| `HeptaTrade/execution/execution_coordinator_reconnect.cpp` | reconnect fences, `ProjectOwnedActiveOrders`, `AuditRecoveryOwner` | coordinator and gateway-composition tests |
| `HeptaTrade/execution/execution_coordinator_terminal.cpp` | terminal recovery owner boundary | coordinator and session-supervisor tests |
| `HeptaTrade/adapter_ib/` | validate callback identities and supply complete active/terminal/execution evidence | `tests/ib_live_terminal_reconciliation_tests.cpp` |
| `HeptaTrade/state/snapshot_refresh_coordinator.*` | non-overlapping refresh generations | `tests/snapshot_refresh_coordinator_tests.cpp` |
| `HeptaTrade/agent/decision_lease_manager.*` | bounded decision ownership, distinct from order idempotency | `tests/decision_lease_manager_tests.cpp`, `tests/execution_decision_lease_authority_tests.cpp` |

**`HeptaTrade/reconcile/reconcile_engine.cpp` is not this engine.** It is the
legacy monolith's CSV reconciliation reporter, called by
`HeptaDemoStrategyTrader.cpp`, and is absent from the canonical core and IB
translation-unit inventory. Its CSV matching and numeric parsing are not
canonical recovery authority. It is owned by `legacy-runtime`.

## Identities and boundaries

A command record is keyed by the validated agent ID, session ID and tool call
ID. The coordinator currently separates those fields with byte `0x1f`; this
internal representation is not a client serialization API. The normalized
request hash binds the contents of that command. Reusing the key with different
content is an idempotency conflict. Reusing an uncertain command returns its
uncertain state, not a fresh send. A resolved prior command returns a duplicate
result referencing its prior outcome.

Venue correlation IDs are service-owned and survive send/restart boundaries.
A broker order ID alone does not establish account, owner, contract or epoch.
The adapter must validate those facts before handing a correlation map to the
coordinator. The map API does not independently reconstruct missing provenance.

The typed Execution protocol (`execution_service_protocol.h/.cpp`) uses `HEX1`,
not the Agent `HTT1` or supervisor `HSS1` protocol. Operations 1–14 are respectively
place, cancel, command status, owner fence, fence release, reconciliation,
service identity, authoritative read, order preview, flatten preview, flatten,
recovery status, recovery audit and terminalization. Requests carry expected
service epoch and fencing generation; stale identity must be rediscovered and
reconciled, not bypassed by changing command IDs.

## Place and flatten recovery

`RecoverFromJournal` resets the projection under the coordinator mutex, obtains
a validated journal replay, applies records, and validates the reconstructed
projection before admission. A replay failure blocks mutations with
`OMS_REPLAY_FAILED`. An unresolved durable send attempt remains uncertain.

`ResolveUncertainPlaceCommands` receives a service-validated
`map<venue_correlation_id, order_id>`, a completeness flag, and an explicit
`resolveMissingAsRejected` policy. It considers uncertain place/flatten records
with a supported recovery reason and a nonempty durable correlation ID.

| Evidence / condition | Result |
|---|---|
| Snapshot incomplete | No resolution; `AUTHORITATIVE_CORRELATION_SNAPSHOT_INCOMPLETE` |
| Exact correlation maps to a nonnegative order ID | Append `execution_command_resolved` as accepted, then update memory and restore owner |
| Correlation absent and `resolveMissingAsRejected=false` | Keep uncertain; absence is not rejection |
| Correlation absent and the venue-specific caller explicitly permits authoritative absence resolution | Append a rejected resolution with `AUTHORITATIVE_CORRELATION_NOT_FOUND` |
| Resolution append fails | Block, return `OMS_EXECUTION_RESOLUTION_WRITE_FAILED`; do not claim success |

A negative ID is not positive acceptance evidence. Callers must not populate
invalid negative placeholder correlations as if they were authoritative facts.
The explicit missing-resolution option is not a generic permission to infer
non-delivery from a timeout or an incomplete order list.

Resolution counts are bookkeeping, not proof of an economic fill. Clearing a
recovery block requires no uncertain commands to remain and the matching block
reason; unrelated safety blocks are not cleared by this operation.

## Cancel recovery and terminal ownership

Cancel recovery separately requires complete active and terminal snapshots.
Terminal IDs must be valid, have a supported terminal status and not also appear
as active. Execution evidence is a separate input. An active order remains
uncertain; order ID zero is not treated as uniquely correlated; absent terminal
and execution evidence is not success. A successfully cancelled terminal order
without economic execution can resolve the cancellation as accepted. A filled
or executed order cannot be represented as successful cancellation merely
because it is no longer active. The resolution journal event precedes the
in-memory change.

`ReconcileOrderOwners` is narrower: given a **complete** active-order set, it
journals `order_owner_reconciled_terminal` before removing each absent owner.
It does not prove how an absent order terminated or that the account is flat.
Final economic reconciliation additionally needs executions, positions, account
state and the corresponding complete refresh barriers.

For example, an uncertain place whose correlation becomes order 101 can resolve
as accepted, while a cancel of 101 remains unresolved until terminal evidence
arrives. If execution evidence shows it filled, removing its active owner does
not undo the position. The final position and flatten path still require their
own authoritative checks.

## Refresh, locks and terminalization

`SnapshotRefreshCoordinator` tracks account-summary, positions and open-order
refreshes. Repeated requests coalesce into at most one subsequent refresh; they
do not overlap generations for callbacks lacking request IDs. `Complete`
returns the completed and optional next generation. `Expire` reports an expired
in-flight generation and whether a request was pending. Consumers must check
these results before publishing completeness.

The coordinator serializes command and owner changes with its mutex. Simulator
reservation/activation follows coordinator then venue lock order; event sinks
run after the venue lock is released. A reentrant callback must not acquire
those locks in reverse order. `TryGetOrderOwner` explicitly distinguishes Busy
from Missing; lock contention cannot be interpreted as missing ownership.

Reconnect fencing refuses to create a new clean reconnect fence while local
owners or another block remain. The recovery owner audit rejects unmapped
active orders, contradictory account/domain ownership and unresolved commands.
Terminal recovery closes callback admission, drains in-flight callbacks,
freezes one authoritative audit view and commits the terminal witness. A timeout
preserves the fence and incomplete state.

## Decision leases are not durable command IDs

The decision lease key is `(executionDomain, account, instrument)`, with
`(agentId, sessionId)` as owner. `Acquire`, `Renew`, `Release`, `Validate` and
`FenceOwner` are mutex-protected. Credentials contain a manager-monotonic fencing
token and a per-key generation. Renew/release/validate require both exact
credentials and owner; an expired or released credential is not reusable.

The manager uses a supplied steady clock and defaults to a 24-hour maximum TTL.
An initial fencing watermark supports recovery; this in-memory manager is not
the supervisor's persistent lease store. Clock failure, exhaustion and stale
fences have distinct statuses. Lease validity never replaces durable command
identity, final risk or broker state validation.

## Testing and operations

Run `./scripts/dev_core.sh`. Coordinator tests exercise actual journal replay,
uncertain sends, idempotency, owner fences and resolution; IB terminal tests
exercise callback/economic evidence; snapshot tests exercise generation
coalescing and expiry. Add tests at the affected durable boundary rather than
checking that a private function name occurs in source.

Operators should retain command ID, request hash, correlation, service and
connection epochs, refresh generations, resolution reason and journal sequence.
Use command-status and recovery audit before considering a new intent. Never
edit a journal or fabricate a complete snapshot to clear an uncertainty.
The installed simulator smoke verifies its own venue and same-artifact slot
transitions, not real Broker qualification or arbitrary N-1 schema rollback.
