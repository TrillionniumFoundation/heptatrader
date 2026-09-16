# Authoritative state and recovery snapshots

Status: CURRENT
Applies to: repository HEAD
Implementation: `HeptaTrade/state/authoritative_trading_snapshot_store.cpp`, `HeptaTrade/state/authoritative_trading_snapshot_store.h`, `HeptaTrade/state/snapshot_refresh_coordinator.cpp`, `HeptaTrade/state/snapshot_refresh_coordinator.h`, `HeptaTrade/state/ib_authoritative_quote_subscription_set.cpp`, `HeptaTrade/state/ib_authoritative_quote_subscription_set.h`, `HeptaTrade/state/ib_contract_identity.cpp`, `HeptaTrade/state/ib_contract_identity.h`
Tests: `tests/authoritative_trading_snapshot_store_tests.cpp`, `tests/snapshot_refresh_coordinator_tests.cpp`

## Responsibilities

The state layer turns venue callbacks and refresh barriers into coherent account, position, open-order, quote, correlation, and recovery snapshots. It prevents a caller from treating a partial callback stream as authoritative trading truth.

## Core semantics

Every authoritative snapshot has, explicitly or by its owning runtime:

- connection epoch;
- refresh generation;
- completeness flag;
- reason code when incomplete;
- observation and freshness bounds where time-sensitive;
- normalized instrument identities;
- provenance to the venue callback/refresh barrier.

An epoch changes when the venue connection identity changes. A generation changes when a new refresh begins. Data from different epochs or incompatible generations must not be combined.

## Snapshot lifecycle

```text
INVALID / INCOMPLETE
  -> REFRESH_REQUESTED
  -> COLLECTING
  -> BARRIER_COMPLETE
  -> COMPLETE
  -> STALE or INVALIDATED on timeout, conflict, disconnect, or new epoch
```

Incremental callbacks may update a complete snapshot only when they can be correlated without conflict. A collision, malformed identity, missing execution evidence, or reconnect invalidates the affected snapshot and requires a fresh barrier.

## Storage

`authoritative_trading_snapshot_store` publishes coherent read views for Gateway tools. It is not the durable order ledger. Durable mutation identity and send state remain in the OMS journal; venue snapshots are re-established after restart and reconciled against replay.

## Quote state

A quote binds instrument, subscription identity, bid, ask, observation time, and stale-after time. Risk rejects unavailable, stale, crossed, non-finite, or non-positive quotes. The final send path revalidates the exact quote binding under the adapter lock.

## Position and order state

Positions must be normalized by contract identity and units. Open and terminal orders require stable broker correlation. Filled terminal orders require execution evidence; an order-status string alone is insufficient for economic fill truth.

For multi-asset risk, the snapshot must expose both native quantity and base-currency notional/margin dimensions rather than summing heterogeneous quantities.

## Recovery

Recovery coordinates a fresh broker barrier, journal replay, active and terminal correlations, position/account state, and owner fences. Any unresolved send attempt, unknown broker order, missing execution, or epoch transition keeps mutation blocked. Terminal recovery closes callback admission, drains in-flight callbacks, freezes one audit snapshot, and durably commits the terminal witness.

## Failure semantics

Timeout, callback conflict, duplicate correlation, missing barrier, invalid contract identity, unsupported unit conversion, or I/O failure produces an incomplete snapshot with a typed reason. Consumers must check completeness and epoch/generation before reading values.

## Observability

The state API and its owning runtime retain current epoch/generation,
completeness, snapshot age, refresh duration, invalidation reason, active and
terminal order counts, unresolved correlations, and post-fill refresh state.
The canonical exported metric inventory is narrower: callback lag, conflict
count, and every snapshot age/generation are still requirements rather than a
delivered metric interface.  They must not be treated as observable merely
because a state object or this document names the corresponding diagnostic.
See the [observability inventory](../OBSERVABILITY-METRICS.md) for the current
producer and collection status.

## Test expectations

Tests cover coherent publication, stale/invalid views, generation rollover, reconnect, out-of-order callbacks, duplicate/colliding correlations, refresh timeout, quote freshness, post-fill refresh, terminal freeze, and concurrent readers.

## Assembly and recovery reference

[`Execution recovery and reconciliation`](../technical/reconciliation-engine.md)
describes the concrete refresh coordinator APIs and downstream resolution
rules. Account-summary, positions and open-orders refreshes coalesce pending
requests without overlapping ambiguous generations. Completion is an explicit
barrier result, not the receipt of one callback. The similarly named historical
CSV reporter has been [retired](../technical/legacy-retirement.md) and is not
part of the maintained snapshot construction.

## Retired parallel IB projection

The repository previously carried six unconnected IB callback and recovery
implementations under `HeptaTrade/state/`: the account-position consumer,
open-order consumer, order projector, recovery coordinator, recovery-event
consumer, and connection-lifecycle state machine (each with a private header
and source file). They had no active include or runtime consumer, were absent
from every CMake target and test, and therefore could not be the source of
authoritative state. They were retired on 2026-09-16 instead of being wired in
as a second state authority.

The maintained authority remains the snapshot store and refresh coordinator,
with contract identity and quote-subscription normalization used by the IB
runtime. The retired source is recoverable from the original repository
snapshot at commit `4fe281d8e29efcf7f75cb8ad4a718b2210a58bfb`; no persisted
state or wire schema depended on these private, unbuilt types.
