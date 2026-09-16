# Typed venue cancellation and uncertain outcomes

Status: CURRENT
Applies to: Execution coordinator, deterministic simulator and IB PAPER candidate

## One result for one attempt

`execution/venue_cancel_result.h` defines the in-process result of the single
`ExecutionCoordinatorCallbacks::cancelOrder` dependency. It is not a new wire
version or OMS schema. The simulator and IB adapter return the same result type.
The old Boolean coordinator callback is removed; there is no implicit Boolean
conversion or second last-error lookup. Authoritative flattening independently
uses the typed `ExecutionCoordinatorCallbacks::flattenOrder` result described by
[venue-flatten-contract.md](venue-flatten-contract.md); neither path samples a
mutable adapter last-error after dispatch.

| Disposition | Meaning | Coordinator outcome |
|---|---|---|
| Submitted | the local venue API accepted dispatch, not terminal cancellation | durable `cancel_sent`, accepted command |
| Deferred | a local intent awaits a current broker acknowledgement | durable `cancel_pending`, uncertain command |
| RejectedBeforeSend | an explicit reason establishes that venue I/O was not invoked | durable rejection; same-ID retry is duplicate |
| Uncertain | an effect may have occurred or no trustworthy outcome was returned | durable pending outcome, admission blocked, reconciliation required |

An empty rejection reason or unrecognized disposition is uncertain. A default
result is uncertain. Exceptions of any type from the callback are uncertain,
including allocation failures after SDK dispatch or after successful local
bookkeeping. The result is captured under the IB API mutex or simulator venue
mutex. A concurrent callback cannot replace its reason through a shared error
string after the lock is released.

## Durable order and backwards-readable recovery

Cancellation still writes its intent and critical `cancel_send_attempt` before
invoking the venue. An uncertain result closes mutation admission before
allocating diagnostics and keeps the existing request identity. It writes the
already-supported critical `cancel` event with `status=cancel_pending` and
`risk_code=RECOVERY_RECONCILE_REQUIRED`; no new event type or compatibility
reader is introduced. A failed pending receipt keeps the command uncertain,
poisons/blocks the existing writer path and leaves the earlier durable attempt
for restart. It never writes a later `reject` solely because dispatch threw.

The same command ID returns uncertain without another call. Conflicting reuse
is rejected. Restart reconstructs the unresolved attempt before new admission.
Only complete authoritative active and terminal snapshots can resolve it.
Absence alone is not proof: an active order remains unresolved, a positively
cancelled terminal order resolves as cancellation, and an economic fill
resolves as target-filled rather than successful cancellation. The resulting
`cancel_command_resolved` record survives another restart.

The generic mutation block already restricts normal mutations; this change
does not invent an emergency bypass for cancels or flatten. Operators must use
existing recovery/authoritative exit contracts. PAPER/LIVE capabilities and
ownership, generation, account/domain checks remain unchanged.

## IB SDK and delayed acknowledgement

Adapter preconditions and lifecycle guards can prove a refusal before I/O.
Once `IIBApiWrapper::CancelOrder` is invoked, a Boolean false is conservatively
uncertain: it is not treated as independent proof that the network saw nothing.
The adapter catches post-dispatch exceptions and returns an uncertain result.
The SDK wrapper is still the lower-level transport interface, not an Execution
result contract.

For deferred cancels, the adapter keeps an unattempted item while the mutation
fence is closed. Once permitted, it removes that item immediately before the
external call. A thrown call therefore cannot leave a queued item that another
acknowledgement resends. Exceptions propagate to the existing event-processing
failure boundary; they do not manufacture terminal proof. The coordinator's
already-durable deferred command remains uncertain until reconciliation.

## Executable regressions and scope

`tests/cancel_uncertainty_cases.h` is executed by the real coordinator test
binary. It covers ordinary/non-standard/allocation exceptions, incomplete or
invalid results, absence versus positive terminal proof, duplicate/conflicting
IDs, writer-path loss after dispatch, and restart before and after resolution.
The historical pre-intent refusal tests remain unchanged in meaning.

`tests/ib_live_terminal_reconciliation_tests.cpp` injects an SDK exception, an
ambiguous Boolean false and an actual allocation failure immediately after
SDK return. It also dispatches repeated acknowledgement events after a deferred
send exception and requires exactly one lower-level attempt. Existing simulator,
Gateway/Unix and installed-process tests exercise the migrated dependency.
These are offline tests with synthetic broker observations; they do not qualify
an IB account. Target-host persistent storage and exact new-revision CI remain
separate evidence. Never describe a tmpfs restart test as a physical power-loss
test.
