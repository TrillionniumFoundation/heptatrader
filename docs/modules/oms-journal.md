# OMS journal and recovery

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `HeptaTrade/oms_journal.cpp`, `HeptaTrade/oms_recover.cpp`  
Tests: `tests/oms_journal_durability_tests.cpp`, `tests/execution_coordinator_tests.cpp`

## Responsibilities

The OMS journal provides the durable append-only record used to prevent duplicate external mutations, recover command status after process failure, preserve owner fences, and explain execution decisions. It is not a market-data database and is not replaced by application logs.

## Current schema

The current public event schema is v2 and is described in `docs/OMS-EVENT-SCHEMA.md`. Core fields include schema version, event type, timestamp, order and request identities, trace identity, venue, account, instrument, side, quantity, price, status, reason, and source.

Execution code also records internal command and control state required for idempotency and recovery. Readers must ignore explicitly extensible unknown event types only when the schema contract permits it; malformed required fields fail recovery.

## Durability contract

For a risk-increasing mutation:

1. normalize the intent;
2. bind owner, session, execution domain, and command ID;
3. append intent;
4. make the record durable;
5. append/durably commit the send attempt;
6. call the venue;
7. append the observed result or uncertain state.

A successful return must not precede the durable state it claims. The journal directory and file must be regular, trusted, and non-symlink paths under the service-owned state directory.

## Idempotency

`event_id` is the preferred event deduplication key. Mutation idempotency is stronger: a command ID binds the normalized mutation and owner. Reusing a command ID for the same mutation returns the durable state; using it for different content is rejected.

Event fallback fingerprints are compatibility behavior for historical v1/v2 data and must not be treated as a globally collision-resistant mutation identity.

## Recovery

Startup replay reconstructs command state and fences before mutation admission opens. Recovery then compares the reconstructed state with authoritative venue orders, executions, positions, and account state. A replayed `send_attempt` without a proven terminal result is uncertain and must be reconciled; it is never automatically resent.

## Schema evolution

A future v3 fill ledger must add stable venue order IDs, execution IDs, incremental and cumulative fill quantity, remaining quantity, average fill price, commission/currency, correction/bust, replace lineage, and venue sequence. Migration must be additive and replay tests must retain v1/v2 fixtures. Until v3 is implemented, PAPER qualification must prove the existing adapter correlation and execution callback path for the bounded campaign.

## Failure semantics

- Append/open/fsync failure before external send: reject and close the mutation gate if durability is no longer trustworthy.
- Failure after a possible send: uncertain, preserve evidence, and reconcile.
- Truncated final record: follow the journal's documented tail-recovery rule; never skip corruption in the middle.
- Duplicate event: count and skip only under the schema's exact deduplication rule.
- Unknown schema version: fail closed.

## Observability

Expose append latency, fsync latency, file size, replay duration, records read, duplicates skipped, corrupt records, unresolved send attempts, and last durable sequence. Application logs may refer to journal sequence and command ID but must not duplicate secrets.

## Test expectations

Tests inject process and I/O failure before and after each durable boundary, verify same-command replay, conflicting-command rejection, tail truncation behavior, duplicate events, restart recovery, and multi-threaded append serialization.
