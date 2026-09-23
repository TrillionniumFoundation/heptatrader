# Execution Service

Status: CURRENT
Applies to: repository HEAD
Implementation: `HeptaTrade/execution`, `HeptaTrade/agent`, `HeptaTrade/events`
Tests: `tests/execution_coordinator_tests.cpp`, `tests/execution_event_feed_tests.cpp`, `tests/execution_decision_lease_authority_tests.cpp`, `tests/send_attempt_time_index_cases.h`, `tests/python/test_send_attempt_time_index.py`, `tests/venue_placement_cases.h`, `tests/recovery_projection_faults.cpp`, `tests/python/test_recovery_projection.py`, `tests/pre_intent_refusal_cases.h`, `tests/python/test_execution_latency_boundaries.py`, `tests/cancel_uncertainty_cases.h`, `tests/oms_recovery_growth_probe.h`, `tests/python/test_venue_place_rejection.py`, `tests/flatten_result_cases.h`, `tests/python/test_execution_reason_metrics.py`, `tests/execution_ipc_scheduling_tests.cpp`

## Responsibilities

The Execution Service is the sole order authority. It validates execution context, applies broker-independent and venue-specific policy, allocates or verifies the stable command identity, commits mutation intent durably before external send, invokes the venue adapter, projects authoritative events, publishes execution events, supports command-status lookup, and coordinates recovery and reconciliation.

Its shared support owns decision leases, execution-event delivery and owner-scoped health publication. The [OMS journal module](oms-journal.md) owns the journal implementation consumed by this service.

No Agent, Gateway, strategy, bridge, or legacy component may send an order around this service.

## Command lifecycle

```text
RECEIVED
  -> PRECHECKED
  -> POLICY_APPROVED
  -> INTENT_DURABLE
  -> SEND_ATTEMPT_DURABLE
  -> SENT | REJECTED | UNCERTAIN
  -> ACKNOWLEDGED / PARTIALLY_FILLED / FILLED / CANCELLED / REJECTED
  -> RECONCILED
```

The exact implementation has more detailed records, but the invariants are:

- a risk-increasing external send never precedes its durable journal record;
- a command ID identifies one normalized mutation for one owner/session;
- a replay of the same command returns durable state rather than sending again;
- a conflicting reuse of a command ID is rejected;
- transport loss after a possible send is `UNCERTAIN`, not a safe retry signal;
- cancel and authoritative flatten use dedicated guarded paths.

## Public contracts

- Typed Unix execution protocol for Gateway requests.
- Execution event feed for ordered state changes and health.
- Read authority for account, positions, orders, quote, health, and command status.
- Control authority for owner fencing, reconciliation, recovery audit, and terminalization.
- Venue contract implemented by the deterministic simulator or a broker adapter.

Protocol fields and reason codes are versioned. Unknown fields, unsupported versions, and oversized frames are rejected.

## Persistence and recovery

The OMS journal is the durable mutation ledger. Startup recovers it before accepting mutations. Without a generation store, the legacy path validates and replays the complete pinned journal before its first recovery callback. With a selected generation store, startup validates the selected generation, lineage, cumulative command/send indexes and active-tail sentinel, replays only the bounded hot set plus active tail, and services older command identities from the pinned disk index on demand. Both paths preserve exact command identity, unresolved sends, owner fences and terminal evidence; corruption or pointer/index drift fails closed rather than falling back to an older generation or treating history as empty.

The coordinator no longer copies a complete replay into a second full-history vector. Generation-backed recovery also avoids repopulating every historical command into the coordinator: historical command lookup uses the cumulative full-key disk index and a bounded cache, while current/unresolved state remains hot. Allocation or projection exceptions clear partial projections and fence mutations; valid uncertain commands remain available for authoritative reconciliation. See [recovery memory and exception semantics](../technical/coordinator-recovery-memory.md) and [OMS recovery capacity](../technical/oms-recovery-capacity.md).

Stopped-state generation maintenance is explicit. It does not run concurrently with the writer, expire command IDs, delete history, grant PAPER/LIVE authority or make a damaged generation ignorable. V2 seals immutable deltas, rotates the active journal to a lineage-bound tail, and provides explicit strict legacy export for downgrade. The active tail is bounded by maintenance cadence, but immutable historical storage remains retained until an explicit external retention policy exists.

Journal failure before send rejects the mutation. Journal failure after a possible send blocks further risk and requires command-status/reconciliation recovery.

The simulator uses two-phase placement: reserve an inert order, establish its owner and projection, durably append `place_sent` with status `activation_pending`, then activate it and durably append `place_activated`. Reserved orders count toward pending risk but cannot submit or fill. A crash without the final activation receipt replays as uncertain. Activation failure or exception appends a later critical `place_outcome_uncertain`, fences mutations, and survives replay; the pending receipt cannot overwrite that uncertainty. Immediate broker adapters leave the optional activation callback unset. V2 stopped-state seals also carry a compact simulator economic checkpoint, so normal restart restores cumulative position/admission/order-ID state without materializing every historical simulator order. An older V2 lineage without that checkpoint is not replayed in full by the runtime; startup fails closed until stopped-state lifecycle sealing reconstructs and publishes the checkpoint.

## Concurrency

The coordinator serializes command identity and durable state transitions. Risk-increasing admission remains one-at-a-time. After its durable send-attempt marker is committed, place/flatten releases the coordinator mutex around the venue call while a process-local in-flight fence rejects another new risk mutation; cancel releases the mutex around its venue call as well. The read-only cancel-eligibility preflight also runs without the coordinator mutex because the real adapter may wait on its API lock. A process-local pre-intent reservation permits only one eligibility check per order at a time; competing command identities fail before any durable intent or venue effect. After the unlocked check returns, command identity, session fencing, mutation-block state and order ownership are revalidated before any cancel intent is persisted. An unlocked place/flatten also carries its exact owner key: owner fencing counts that unresolved venue boundary as affected and fence release fails closed until the dispatch returns, so a revocation cannot observe a false zero before the accepted order owner is projected. Status, reconciliation and control reads can therefore proceed while a provider or adapter preflight is slow; terminal finalization and broker reconnect both refuse to cross a venue-effect boundary while any venue dispatch remains in flight. Venue callbacks may arrive concurrently and out of order; adapters normalize them into monotonic projections where possible and mark conflicts incomplete. Callback admission is explicitly closed and drained during terminal recovery.

Generation index files are immutable and descriptor-pinned for one selected generation. Historical command lookup uses a binary search over the sorted command index. Current V2 historical send-attempt lookup binary-lower-bounds the account/domain/time ordered index for the exact requested cutoff and then streams only that subject window with the exact-offset sequential reader. A changed subject or backwards cutoff performs another exact lower-bound; it does not reset the historical budget or require a complete current-format scan. Legacy unsorted generations retain the conservative complete compatibility scan.

## Failure semantics

- Invalid owner/session/domain/lease: reject.
- Stale or invalid authoritative quote: reject.
- Incomplete risk snapshot: reject.
- Kill-switch uncertain or engaged: block risk increase.
- Duplicate same mutation: return durable prior result.
- Duplicate conflicting mutation: reject.
- Send exception or timeout after durable attempt: uncertain and reconcile.
- Venue callback conflict or reconnect: invalidate affected snapshots and re-establish an authoritative barrier.
- Selected generation/index/sentinel integrity failure: block recovery and mutation; never reinterpret the store as absent.

## Observability

Required records include command ID, owner/session, execution domain, normalized instrument, lifecycle state, journal sequence, send-attempt state, venue order correlation, reason code, and timing. The actual coordinator exposes fixed place/cancel/authoritative-flatten result bins, operation latency, full coordinator replay/projection latency and O(1) container-size gauges through the existing daemon observation/report path. See [runtime cost observations](../technical/runtime-cost-observations.md).

Coordinator outcome reasons, IB callback ingress lag/conflict, quote/snapshot age, successful post-fill Broker reconciliation duration, reconnect/refresh duration and privileged network-policy readback are implemented for the bounded current scope and tracked in the [implementation inventory](../OBSERVABILITY-METRICS.md). Broader portfolio/PnL/drawdown and deeper product lifecycle metrics remain separately deferred. Source-side producers and presence flags must not be presented as target-host collection, receiver delivery or trading-authorization evidence.

## Test expectations

Tests cover idempotency, journal-before-send, conflicting command IDs, send exceptions, cancel, reconnect, recovery, owner fencing, event ordering, transport failure, simulator end-to-end behavior, and terminal paths. Every new mutation state needs restart tests at each durable boundary.

Generation tests additionally cover V1/V2 verification, repeated generation cuts, pointer/sentinel/lineage corruption, ancient duplicate/conflict lookup, no second venue send, send-attempt continuity, bounded hot recovery and explicit downgrade export. PAPER terminal evidence also carries compact HPM2 history counts through the supervisor witness and external-halt capsule as full unsigned counters; the old 4,096 limit remains only on bounded owner/protocol or legacy HPM1 structures, not permanent mutation-history cardinality. Send-window tests must preserve strict `timestamp > cutoff`, insertion/sequence semantics, exclusion of active-tail duplicates, exact sorted lower-bound behavior for forward and backwards cutoffs, and conservative full-scan compatibility for legacy unsorted indexes.

### Pre-intent refusals versus retained command identity

Place/cancel context, ownership, expiry, missing-capability and blocked-entry refusals before any journal intent return a typed rejection without inserting a new command into the permanent projection. Invalid-context/hash flatten refusals also have no retained identity. Status lookup is absent for such never-admitted IDs. The same normalized intent can be evaluated again after the actual required authority changes; the client still must not replace a possibly-sent command ID.

Durably rejected, accepted and uncertain commands are never expired merely to reduce memory. Flatten rejections using the dedicated durable `flatten_reject` path are retained too; they must not be confused with no-record refusals. `RejectLocked` remains the post-intent/persistent rejection helper. With generation-backed recovery, sealed historical identities remain on disk and are loaded on demand into a bounded historical cache instead of being materialized wholesale. Without a generation store, the legacy full-history path remains supported and retains its complete replay cost.

## Known limitations

LIVE is unavailable. IB PAPER remains qualification-gated. CTP and XT do not implement real transports and may not be wired as authoritative venues.

Generation V2 bounds the active replay working set and historical command cache, not every long-horizon operation. Immutable history and cumulative indexes still grow between maintenance rebases; stopped-state rebase can collapse the lineage and safely prune now-unreferenced cumulative-index copies without expiring identity or event history. Ordered historical send-window queries use the declared account/domain/time index to enter the relevant immutable suffix rather than requiring a full scan for ordinary current-format lookups; legacy unsorted generations retain the conservative compatibility scan. Terminal PAPER mutation-manifest construction lower-bounds the sorted command index to the exact owner/session prefix, streams that sealed range into a compact digest, and combines it with bounded active-tail records rather than materializing a lifetime mutation vector.

## Developer map and recovery examples

[`Execution recovery and reconciliation`](../technical/reconciliation-engine.md) maps entry points and tests, explains exact correlation resolution and guarded absence handling, distinguishes cancel resolution from economic fills, and specifies refresh coalescing, owner terminalization and decision lease roles. [`Execution events`](../technical/execution-events.md) owns the stream cursor and backpressure contract. The old monolith's CSV reporter has been [retired](../technical/legacy-retirement.md); maintained recovery is unchanged.

## Wire fields and versioned examples

[`wire-operation-contracts.md`](../technical/wire-operation-contracts.md) specifies HEX1 v11's exact per-operation field sets, value representations, identity requirements and failure actions. Its in-memory golden vectors use real C++ codecs, not a documentation keyword check.

## Send-attempt query cost

The live in-memory send-attempt index avoids a full journal scan for current-tail account/domain time-window queries. Current V2 sealed history declares the account/domain/time sort order: native lookup performs a binary lower-bound for the requested subject/cutoff, then streams only that ordered suffix with an exact-offset buffered reader. A changed account/domain or backwards cutoff simply performs another lower-bound against the requested historical window; it does not reset the rolling budget. Legacy unsorted generations retain the conservative complete compatibility scan, now also buffered sequentially rather than rereading a random-probe window for every row.

## Typed venue binding

[Venue placement contract](../technical/venue-placement-contract.md) specifies the single Immediate/Reserving dependency, typed outcomes, constructor rejection, lock-bound IB result capture, migrated callers and uncertainty/replay tests. Rejection classification is independent of diagnostic prose; the existing wire and journal reason strings are unchanged. Unknown classifications remain uncertain.

[Venue cancellation contract](../technical/venue-cancellation-contract.md) defines Submitted, Deferred, RejectedBeforeSend and Uncertain. The single typed cancel dependency replaces a Boolean and mutable last-error lookup. Exceptions after a possible send remain journaled as pending and are never retried blindly; positive authoritative terminal evidence, not absence alone, resolves them. Existing durable and wire contracts are unchanged.

## Measured wait and retired callback

The [cost contract](../technical/runtime-cost-observations.md) distinguishes coordinator lock wait, lock-held work and inclusive local-operation time with old-producer presence handling. `trackOrder` and its two optional dispatch calls were removed after their watchdog consumer was retired. Actual owner projection, `onIbOrderPlaced`, durable receipts, uncertain outcomes and reconciliation remain unchanged. This private composition cleanup is not a promise of source compatibility with an independently maintained experimental caller.

## Atomic authoritative-flatten result

The [flatten result contract](../technical/venue-flatten-contract.md) completes the typed placement/cancellation boundary. The coordinator no longer carries `lastIbRejectReason` or a Boolean/out-ID flatten callback. The adapter captures classification, detail and known order identity under the send lock; possible SDK calls and post-send bookkeeping failures remain durably uncertain. The existing no-op proof, schema, owner fences, guarded exits and reconciliation are unchanged. The existing OMS stream also exports bounded coordinator reason bins; these are not complete profile/risk or Broker lifecycle telemetry.

## Bounded IPC scheduling

The command socket now uses one nonblocking framing/reply reactor (64 live
connections, at most 1 MiB per configured request) and two FIFO authority lanes
(24 queued requests per lane). Partial frames and slow readers consume bounded
connection slots, not authority workers. The same accept-time steady deadline
covers framing, queueing and reply; expired queued calls never enter authority.
Overload closes the connection without fabricating a command result.

Identity, command status, recovery status and owner fence/release share the
control lane. General authoritative reads stay on the ordinary lane so a slow
snapshot provider cannot occupy the reserved control worker. Other operations retain a single ordered
lane. Fence/release stay in the same FIFO so an old release cannot overtake a
completed newer fence. Identity, trust-domain binding and readiness are checked
again at dispatch. The coordinator remains the only durable mutation owner.
IB status/fence no longer take the policy dispatch mutex; IB fence release uses
try-lock and returns `IB_PAPER_CONTROL_BUSY` without removing the fence while a
policy operation is active.

A timeout after dispatch only ends the reply channel. It never detaches,
retries or pretends to cancel a possibly-effectful authority call. Stop closes
admission/transports and joins every authority lane outside the lifecycle lock;
a callback can request Stop without self-join. An indefinitely stuck in-process
provider still requires process-level recovery, not unsafe thread abandonment.
Local journal/fsync and control callbacks must themselves be bounded for an
operational latency SLO; this scheduler is not a hard-real-time claim.

`tests/execution_ipc_scheduling_tests.cpp` exercises partial-frame concurrency,
slow authority with query/fence access, queued expiry, lost replies, concurrent
Stop/callback Stop and restart. Its real coordinator/socket fixture proves that
an in-flight venue call remains visible to fencing, fence release is rejected,
exact-ID replay sends once and the owner fence survives journal recovery.

## Control result domains

`ExecutionControlAuthority` now returns `ExecutionControlStatusResult` from
query, fence, fence-release and reconcile operations. Recovery audit returns
`ExecutionOwnerAuditResult`. The simulator policy, IB PAPER policy, IPC client,
Gateway and Supervisor recovery callers consume these same types; ordinary
status cannot carry an owner-completeness assertion or a terminal witness.
This is an internal source-API change, not a new installed StrategyClient API.

`ExecutionControlResult` remains the explicit HEX1 v11 codec/terminal-operation
envelope. Server dispatch widens a narrow result explicitly with absent/default
unrelated evidence; it does not infer or copy terminal authority from status.
Existing wire fields, HSL/OMS formats, final-use checks and recovery ordering are
unchanged. The real IPC regression injects unrelated audit/terminal fields in a
synthetic underlying response and verifies that ordinary status transmits only
its own result and exact command/service identity. Contract static assertions
prevent implicit widening or restoration of the broad virtual status API.
