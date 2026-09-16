# Execution Service

Status: CURRENT
Applies to: repository HEAD
Implementation: `HeptaTrade/execution`, `HeptaTrade/agent`, `HeptaTrade/events`
Tests: `tests/execution_coordinator_tests.cpp`, `tests/execution_event_feed_tests.cpp`, `tests/execution_decision_lease_authority_tests.cpp`, `tests/send_attempt_time_index_cases.h`, `tests/python/test_send_attempt_time_index.py`, `tests/venue_placement_cases.h`, `tests/recovery_projection_faults.cpp`, `tests/python/test_recovery_projection.py`, `tests/pre_intent_refusal_cases.h`, `tests/python/test_execution_latency_boundaries.py`, `tests/cancel_uncertainty_cases.h`, `tests/oms_recovery_growth_probe.h`, `tests/python/test_venue_place_rejection.py`, `tests/flatten_result_cases.h`, `tests/python/test_execution_reason_metrics.py`

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

The simulator uses two-phase placement: reserve an inert order, establish its owner and projection, durably append `place_sent` with status `activation_pending`, then activate it and durably append `place_activated`. Reserved orders count toward pending risk but cannot submit or fill. A crash without the final activation receipt replays as uncertain. Activation failure or exception appends a later critical `place_outcome_uncertain`, fences mutations, and survives replay; the pending receipt cannot overwrite that uncertainty. Immediate broker adapters leave the optional activation callback unset.

## Concurrency

The coordinator serializes command identity and durable state transitions. Venue callbacks may arrive concurrently and out of order; adapters normalize them into monotonic projections where possible and mark conflicts incomplete. No lock should be held across an unbounded broker call. Callback admission is explicitly closed and drained during terminal recovery.

Generation index files are immutable and descriptor-pinned for one selected generation. Historical command lookup uses a binary search over the sorted command index. Historical send-attempt window lookup performs one complete scan when a generation/account/domain window is first requested (or when the cutoff moves backwards), then caches only the matching suffix and prunes it for monotonically increasing cutoffs. Ordinary forward-moving rate checks therefore do not rescan permanent history for every order; a backwards clock preserves the exact historical semantics by deliberately falling back to a complete scan.

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

Detailed per-reason lifecycle, callback/quote age, end-to-end Broker reconciliation and network-policy metric producers remain tracked in the [implementation inventory](../OBSERVABILITY-METRICS.md). Source-side presence flags must not be presented as target-host collection or notification evidence.

## Test expectations

Tests cover idempotency, journal-before-send, conflicting command IDs, send exceptions, cancel, reconnect, recovery, owner fencing, event ordering, transport failure, simulator end-to-end behavior, and terminal paths. Every new mutation state needs restart tests at each durable boundary.

Generation tests additionally cover V1/V2 verification, repeated generation cuts, pointer/sentinel/lineage corruption, ancient duplicate/conflict lookup, no second venue send, send-attempt continuity, bounded hot recovery and explicit downgrade export. Send-window tests must preserve strict `timestamp > cutoff`, insertion/sequence semantics, exclusion of active-tail duplicates, monotonically increasing cutoff pruning and full-rescan correctness after a backwards cutoff.

### Pre-intent refusals versus retained command identity

Place/cancel context, ownership, expiry, missing-capability and blocked-entry refusals before any journal intent return a typed rejection without inserting a new command into the permanent projection. Invalid-context/hash flatten refusals also have no retained identity. Status lookup is absent for such never-admitted IDs. The same normalized intent can be evaluated again after the actual required authority changes; the client still must not replace a possibly-sent command ID.

Durably rejected, accepted and uncertain commands are never expired merely to reduce memory. Flatten rejections using the dedicated durable `flatten_reject` path are retained too; they must not be confused with no-record refusals. `RejectLocked` remains the post-intent/persistent rejection helper. With generation-backed recovery, sealed historical identities remain on disk and are loaded on demand into a bounded historical cache instead of being materialized wholesale. Without a generation store, the legacy full-history path remains supported and retains its complete replay cost.

## Known limitations

LIVE is unavailable. IB PAPER remains qualification-gated. CTP and XT do not implement real transports and may not be wired as authoritative venues.

Generation V2 bounds the active replay working set and historical command cache, not every long-horizon operation. Immutable history and cumulative indexes still grow on disk. A first historical send-window query after generation selection, an account/domain change, or a backwards cutoff scans the pinned cumulative send index once before subsequent forward cutoffs reuse the bounded suffix. Terminal PAPER mutation-manifest construction is separately bounded by its campaign manifest contract; that bounded qualification artifact must not be described as an unlimited production-history ledger.

## Developer map and recovery examples

[`Execution recovery and reconciliation`](../technical/reconciliation-engine.md) maps entry points and tests, explains exact correlation resolution and guarded absence handling, distinguishes cancel resolution from economic fills, and specifies refresh coalescing, owner terminalization and decision lease roles. [`Execution events`](../technical/execution-events.md) owns the stream cursor and backpressure contract. The old monolith's CSV reporter has been [retired](../technical/legacy-retirement.md); maintained recovery is unchanged.

## Wire fields and versioned examples

[`wire-operation-contracts.md`](../technical/wire-operation-contracts.md) specifies HEX1 v10's exact per-operation field sets, value representations, identity requirements and failure actions. Its in-memory golden vectors use real C++ codecs, not a documentation keyword check.

## Send-attempt query cost

The live in-memory send-attempt index avoids a full journal scan for current-tail account/domain time-window queries. For sealed history, the generation store's pinned cumulative send-attempt index is immutable. The first query for a generation/account/domain/cutoff scans that index and caches only attempts newer than the cutoff. Later queries for the same subject with a monotonically increasing cutoff prune the cache in memory and do not rescan permanent history. A subject change or backwards cutoff invalidates that optimization and performs a complete exact scan so backwards-clock semantics are preserved rather than silently resetting the rate budget.

## Typed venue binding

[Venue placement contract](../technical/venue-placement-contract.md) specifies the single Immediate/Reserving dependency, typed outcomes, constructor rejection, lock-bound IB result capture, migrated callers and uncertainty/replay tests. Rejection classification is independent of diagnostic prose; the existing wire and journal reason strings are unchanged. Unknown classifications remain uncertain.

[Venue cancellation contract](../technical/venue-cancellation-contract.md) defines Submitted, Deferred, RejectedBeforeSend and Uncertain. The single typed cancel dependency replaces a Boolean and mutable last-error lookup. Exceptions after a possible send remain journaled as pending and are never retried blindly; positive authoritative terminal evidence, not absence alone, resolves them. Existing durable and wire contracts are unchanged.

## Measured wait and retired callback

The [cost contract](../technical/runtime-cost-observations.md) distinguishes coordinator lock wait, lock-held work and inclusive local-operation time with old-producer presence handling. `trackOrder` and its two optional dispatch calls were removed after their watchdog consumer was retired. Actual owner projection, `onIbOrderPlaced`, durable receipts, uncertain outcomes and reconciliation remain unchanged. This private composition cleanup is not a promise of source compatibility with an independently maintained experimental caller.

## Atomic authoritative-flatten result

The [flatten result contract](../technical/venue-flatten-contract.md) completes the typed placement/cancellation boundary. The coordinator no longer carries `lastIbRejectReason` or a Boolean/out-ID flatten callback. The adapter captures classification, detail and known order identity under the send lock; possible SDK calls and post-send bookkeeping failures remain durably uncertain. The existing no-op proof, schema, owner fences, guarded exits and reconciliation are unchanged. The existing OMS stream also exports bounded coordinator reason bins; these are not complete profile/risk or Broker lifecycle telemetry.
