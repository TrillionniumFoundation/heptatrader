# OMS journal and recovery

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `HeptaTrade/oms_journal.cpp`, `HeptaTrade/oms_journal.h`, `HeptaTrade/oms_generation_store.h`, `scripts/verify_oms_journal_replay.py`, `HeptaTrade/oms_capacity_observation.h`, `HeptaTrade/oms_latency_observation.h`, `scripts/hepta_oms_report.py`, `HeptaTrade/oms_archive_codec.h`, `scripts/oms_archive_codec.py`, `scripts/hepta_oms_archive.py`, `scripts/hepta_oms_checkpoint.py`, `scripts/hepta_oms_lifecycle.py`
Tests: `tests/oms_journal_durability_tests.cpp`, `tests/oms_journal_schema_v4_tests.cpp`, `tests/execution_coordinator_tests.cpp`, `tests/python/test_oms_capacity.py`, `tests/oms_live_capacity_cases.h`, `tests/oms_runtime_observation_cases.h`, `tests/python/test_oms_observation_faults.py`, `tests/python/test_oms_operational_report.py`, `tests/oms_queue_budget_cases.h`, `tests/oms_archive_cases.h`, `tests/python/test_oms_archive.py`, `tests/python/test_oms_checkpoint.py`, `tests/python/test_oms_lifecycle_rotation.py`, `tests/compat/oms_recover.cpp`, `tests/compat/oms_recover.h`

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

`OmsRecover` is a test-only lightweight compatibility projection in
`tests/compat/oms_recover.h/.cpp`. The coordinator regression target compiles it
explicitly; installed runtime targets do not. Its historical event and
deduplication behavior is retained unchanged. Production schema 1–4 reading
remains in `OmsJournal`; moving this test helper does not retire a persisted
format or introduce another PAPER recovery authority.

### Generation-backed incremental recovery

Two stopped-state generation formats are supported by one native
`OmsGenerationStore` authority.

Generation v1 preserves an immutable verified full-journal prefix, a full-key
permanent command index, a durable send-attempt index, a bounded hot replay and
a digest-bound runtime manifest. It remains readable for compatibility.

Generation v2 is the long-horizon format. Each generation seals only the JSONL
records added since its parent, binds the parent generation by the exact parent
`manifest.json` SHA-256, carries cumulative full-key command and send-attempt
indexes, and writes a bounded hot replay for unresolved/current state. After all
generation files and their directory are durable, stopped-state maintenance
atomically replaces the active journal with a non-JSON lineage sentinel followed
only by future JSONL events. The sentinel is bound by length and SHA-256 in the
selected v2 runtime manifest. Consequently an older full-ledger reader rejects a
rotated tail rather than mistaking it for an empty or complete ledger.

`CURRENT` and `CURRENT.runtime` remain the selected-generation authority.
Publication order is generation durability, prepared tail durability, atomic tail
path replacement plus parent-directory sync, `CURRENT`, then digest-bound
`CURRENT.runtime`. A crash before tail publication leaves the previous complete
journal. A crash after tail publication but before both pointers agree leaves a
state that fails closed. Native startup never silently selects a parent or treats
an integrity failure as a missing command.

When a generation store is present, the coordinator verifies the selected
manifest, lineage, pinned indexes and active-tail sentinel, replays only bounded
hot events plus bytes after the sentinel, and services historical command-ID
lookups from the cumulative pinned disk index. The permanent index compares the
complete `(agent, session, command)` key and canonical request hash. Historical
lookups enter only a bounded cache; they are not repopulated wholesale into the
coordinator. Rolling send-rate recovery combines the cumulative generation
send-attempt index with current-tail attempts, so a cut cannot reset the mutation
budget. Terminal mutation-universe construction likewise includes historical
durable mutations from the disk index.

The v2 producer stream-merges parent command and send-attempt indexes with the
new tail instead of materializing the complete historical event stream in RAM.
The cumulative send-attempt index is globally ordered for account/domain/time-window
lower-bound lookup, so PAPER rate checks do not rescan all sealed sends on each
preview/place call. Older V2 generations without the order declaration retain a
fail-safe compatibility scan until the next seal.

Command identity is never expired by generation maintenance. Immutable parent
segments remain available until an explicit external retention policy exists;
the active writer path itself no longer grows with sealed terminal history.
Terminal shutdown likewise streams sealed mutation history into HPM2 digest/count
bindings and adds only the active tail; it does not rebuild a lifetime command
vector or emit one manifest row per command. HPM1 remains readable compatibility.

Generation creation requires expanded plain JSONL input for the initial v1-to-v2
migration. A gzip journal must first use the existing lossless stopped-state
expansion; maintenance never guesses a cut inside compressed storage.

### Explicit downgrade export

`scripts/hepta_oms_lifecycle.py export` walks the digest-bound generation chain,
concatenates the newest applicable v1 base, all subsequent v2 delta segments,
and the current active JSONL tail after its lineage sentinel into a new private
complete JSONL file. The output is create-only, fsync'd, and passed through the
strict legacy record validator before success. Export has
`authorization_effect=NONE`; it does not replace the active journal automatically
or grant PAPER/LIVE admission. Downgrade is therefore an explicit stopped-state
operator action rather than silent fallback from a format an older runtime does
not understand.

## Failure semantics

- Append/open/fsync failure before external send: reject and close mutation admission if durability is no longer trustworthy.
- Failure after a possible send: uncertain, preserve evidence, and reconcile.
- Truncated or malformed journal content: follow the strict replay rule; never skip corruption in the middle and continue as healthy.
- Duplicate event: count and skip only under the exact deduplication rule.
- Unknown semantics: retain evidence but do not manufacture an authoritative state transition.
- A present generation store with unsafe metadata, digest drift, parent-binding drift, index corruption, lineage-sentinel mismatch, malformed hot replay or pointer disagreement is a recovery failure; it is never ignored as if no checkpoint existed.

## Observability

Implemented capacity output exposes written bytes/records, headroom, pending records and poisoned/unknown state. The [runtime cost contract](../technical/runtime-cost-observations.md) adds process-instance append, file-data-sync and replay-validation counts, total/maximum/last nanoseconds. Failed attempts are measured too; these are not durability receipts or latency percentiles.

A complete interface for deduplication/corruption/unresolved-command counters, durable sequence and full recovery SLOs remains subject to the [metric inventory](../OBSERVABILITY-METRICS.md). Validation latency excludes application callbacks and Broker reconciliation. Never duplicate secrets in application logs.

## Test expectations

Tests inject path replacement and I/O failure, verify synchronous critical durability, callback-atomic replay, same-command replay, conflicting-command rejection, malformed records, restart recovery, and complete v4 broker-field round trips. New schema fields require both old-fixture and current-writer tests.

Generation tests cover v1 source-prefix identity, v2 lineage-bound active-tail rotation, current-pointer interruption, crash points before and after tail publication, sidecar and parent digest drift, repeated generations, cumulative full-key duplicate/conflict lookup, hot unresolved state, send-attempt continuity, and explicit downgrade export. Native coordinator acceptance uses the v2 stopped-state producer and proves that an ancient disk-backed command is returned as duplicate for an identical payload, rejected as `IDEMPOTENCY_KEY_CONFLICT` for changed content, does not call the venue, and coexists with new active-tail mutation admission.

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
exit evidence. Generation v2 changes the stopped-state storage lifecycle but does
not weaken writer durability or command identity.

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
it is not online truncation or a general N-1 compatibility claim. Generation v2
is a separate stopped-state history-sealing mechanism with an explicit downgrade
export path; neither mechanism runs concurrently with a writer.
