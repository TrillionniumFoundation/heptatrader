# Execution Service

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `HeptaTrade/execution/`, `HeptaTrade/agent/decision_lease_manager.cpp`, `HeptaTrade/events/execution_event_hub.cpp`, `HeptaTrade/events/owner_scoped_health_publisher.cpp`
Tests: `tests/execution_coordinator_tests.cpp`, `tests/execution_event_feed_tests.cpp`, `tests/execution_decision_lease_authority_tests.cpp`

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

The OMS journal is the durable mutation ledger. Startup replays it before accepting mutations. The service reconstructs command identities, send attempts, owner fences, and terminal states, then reconciles with the selected venue. State that cannot be proven from journal plus authoritative venue data remains blocked.

Journal failure before send rejects the mutation. Journal failure after a possible send blocks further risk and requires command-status/reconciliation recovery.

The simulator uses two-phase placement: reserve an inert order, establish its owner and projection, durably append `place_sent` with status `activation_pending`, then activate it and durably append `place_activated`. Reserved orders count toward pending risk but cannot submit or fill. A crash without the final activation receipt replays as uncertain. Activation failure or exception appends a later critical `place_outcome_uncertain`, fences mutations, and survives replay; the pending receipt cannot overwrite that uncertainty. Immediate broker adapters leave the optional activation callback unset.

## Concurrency

The coordinator serializes command identity and durable state transitions. Venue callbacks may arrive concurrently and out of order; adapters normalize them into monotonic projections where possible and mark conflicts incomplete. No lock should be held across an unbounded broker call. Callback admission is explicitly closed and drained during terminal recovery.

## Failure semantics

- Invalid owner/session/domain/lease: reject.
- Stale or invalid authoritative quote: reject.
- Incomplete risk snapshot: reject.
- Kill-switch uncertain or engaged: block risk increase.
- Duplicate same mutation: return durable prior result.
- Duplicate conflicting mutation: reject.
- Send exception or timeout after durable attempt: uncertain and reconcile.
- Venue callback conflict or reconnect: invalidate affected snapshots and re-establish an authoritative barrier.

## Observability

Required records include command ID, owner/session, execution domain, normalized instrument, lifecycle state, journal sequence, send-attempt state, venue order correlation, reason code, and timing. Metrics must separate accepted, rejected, duplicate, uncertain, journal failure, send failure, reconciliation, and recovery duration.

## Test expectations

Tests cover idempotency, journal-before-send, conflicting command IDs, send exceptions, cancel, reconnect, recovery, owner fencing, event ordering, transport failure, simulator end-to-end behavior, and terminal paths. Every new mutation state needs restart tests at each durable boundary.

## Known limitations

LIVE is unavailable. IB PAPER remains qualification-gated. CTP and XT do not implement real transports and may not be wired as authoritative venues.
