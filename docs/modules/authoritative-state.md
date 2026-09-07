# Authoritative state and recovery snapshots

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `HeptaTrade/state/`  
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

Expose current epoch/generation, completeness, snapshot age, refresh duration, callback lag, invalidation reason, conflict count, number of active/terminal orders, unresolved correlations, and post-fill refresh state.

## Test expectations

Tests cover coherent publication, stale/invalid views, generation rollover, reconnect, out-of-order callbacks, duplicate/colliding correlations, refresh timeout, quote freshness, post-fill refresh, terminal freeze, and concurrent readers.
