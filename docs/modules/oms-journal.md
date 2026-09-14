# OMS journal and recovery

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `HeptaTrade/oms_journal.cpp`, `HeptaTrade/oms_recover.cpp`  
Tests: `tests/oms_journal_durability_tests.cpp`, `tests/oms_journal_schema_v4_tests.cpp`, `tests/execution_coordinator_tests.cpp`

## Responsibilities

The OMS journal provides the durable append-only record used to prevent duplicate external mutations, recover command status after process failure, preserve owner fences, correlate venue outcomes, and explain execution decisions. It is not a market-data database and is not replaced by application logs.

## Current schema

`OmsJournal::kSchemaVersion` is **4**. The complete field and event contract is [`../OMS-EVENT-SCHEMA.md`](../OMS-EVENT-SCHEMA.md).

In addition to core command, account, instrument, quantity, price, status, reason, and source fields, v4 persists canonical request hashes, service-owned venue correlations, broker callback type, service and connection epochs, broker request/error evidence, advanced rejection text, hold reason, execution ID, remaining quantity, and market-cap price.

The writer emits finite numeric values and escaped JSON strings. The parser validates object syntax, supplies compatibility defaults for missing historical fields, and retains the raw line for audit. Consumers must not infer semantics from fields or event types they do not understand.

## Durability contract

For a risk-increasing mutation:

1. normalize the intent;
2. bind owner, session, execution domain, command ID, and request hash;
3. append intent;
4. make the record durable;
5. append and durably commit the send attempt;
6. call the venue;
7. append the observed result, broker callback, or uncertain state.

A successful return must not precede the durable state it claims. The journal directory and file must be regular, trusted, and non-symlink paths under the service-owned state directory. Path replacement, unsafe metadata, or synchronization failure poisons further writes.

## Idempotency

`event_id` is the preferred event deduplication key. Mutation idempotency is stronger: a command ID plus normalized request hash binds one mutation and owner. Reusing a command ID for the same mutation returns durable state; using it for different content is rejected.

Fallback event fingerprints are compatibility behavior for historical data and must not be treated as globally collision-resistant mutation identity.

## Recovery

Startup replay reconstructs command state and fences before mutation admission opens. Recovery then compares reconstructed state with authoritative venue orders, executions, positions, account state, connection epoch, and refresh generations. A replayed send attempt without a proven terminal result is uncertain and must be reconciled; it is never automatically resent.

Broker `Filled` text alone is not economic fill proof when the venue contract requires an execution ID and positive execution evidence. Terminal and active correlations remain separate until reconciliation proves their relationship.

`OmsRecover` remains a lightweight compatibility projection used by selected tests. Its old CSV-reporter consumer has been retired; the reader itself is preserved. It is not the complete canonical PAPER recovery authority.

## Failure semantics

- Append/open/fsync failure before external send: reject and close mutation admission if durability is no longer trustworthy.
- Failure after a possible send: uncertain, preserve evidence, and reconcile.
- Truncated or malformed journal content: follow the strict replay rule; never skip corruption in the middle and continue as healthy.
- Duplicate event: count and skip only under the exact deduplication rule.
- Unknown semantics: retain evidence but do not manufacture an authoritative state transition.

## Observability

Implemented capacity output exposes written bytes/records, headroom, pending records and poisoned/unknown state. The [runtime cost contract](../technical/runtime-cost-observations.md) adds process-instance append, file-data-sync and replay-validation counts, total/maximum/last nanoseconds. Failed attempts are measured too; these are not durability receipts or latency percentiles.

A complete interface for deduplication/corruption/unresolved-command counters, durable sequence and full recovery SLOs remains subject to the [metric inventory](../OBSERVABILITY-METRICS.md). Validation latency excludes application callbacks and Broker reconciliation. Never duplicate secrets in application logs.

## Test expectations

Tests inject path replacement and I/O failure, verify synchronous critical durability, callback-atomic replay, same-command replay, conflicting-command rejection, malformed records, restart recovery, and complete v4 broker-field round trips. New schema fields require both old-fixture and current-writer tests.

## Recovery resource contract

See [`OMS recovery capacity`](../technical/oms-recovery-capacity.md) for explicit
byte/count/record limits, failure reasons, read-only capacity diagnostics and
operator recovery actions. Budget failures preserve every journal byte and
command identity; they are never permission to reset the ledger.

## Online capacity observations

Both Execution daemons emit identifier-free structured capacity observations.
The [online capacity contract](../technical/oms-live-capacity.md) defines written
bytes/records, pending records, unknown values, thresholds, sampling and safe
restart/checkpoint actions. No capacity threshold truncates history or blocks
exit evidence. The current persisted schema and recovery authority are unchanged.

## Operational reporting

The installed [OMS report](../technical/oms-operational-report.md) reads bounded
service-log exports, estimates same-epoch growth and produces executable alert
classification and fixed-cardinality histogram text. It never changes journal
bytes or grants admission. Deployment notification delivery remains external.

## Pending memory budget

See [OMS pending queue](../technical/oms-pending-queue.md) for byte/count limits,
backpressure without evicting accepted records, preserved critical exit writes
and the additive online occupancy fields. This is not disk compaction.

## Storage maintenance

See [lossless stopped-state maintenance](../technical/oms-archive-lifecycle.md) for optional gzip storage,
writer exclusion, decoded recovery budgets, crash handling and explicit
expansion before downgrade. It preserves all event bytes and command identities;
it is not online truncation or a general N-1 compatibility claim.
