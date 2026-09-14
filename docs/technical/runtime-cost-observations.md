# Runtime cost observations and send-attempt indexing

Status: CURRENT
Applies to: coordinator secondary index and OMS process-instance observations
Implementation: `HeptaTrade/execution/send_attempt_time_index.h`, `HeptaTrade/oms_latency_observation.h`, `HeptaTrade/oms_journal.cpp`, `HeptaTrade/oms_capacity_observation.h`
Tests: `tests/python/test_send_attempt_time_index.py`, `tests/oms_runtime_observation_cases.h`, `tests/python/test_oms_observation_faults.py`

## Send-attempt query contract

`ExecutionCoordinator::GetPlaceSendAttemptTimes` retains its existing signature,
account/domain isolation, strict `timestamp > cutoff` rule and insertion-order
result. The coordinator mutex serializes reads, live place/flatten insertion
and recovery. The caller's durable command records and send-attempt identity
set remain authoritative; this index never permits a resend.

`SendAttemptTimeIndex` stores a map keyed by the pair `(account, domain)` and a
per-scope timestamp multimap. Each send contributes its timestamp and insertion
ordinal. It does not retain or copy the complete caller Record, request key or
per-record account/domain strings. Those duplicate payloads were not consumed
by any index operation. Scope strings are stored once per scope, and an ordinal
counter replaces the old full-record vector.

Queries use `upper_bound(cutoff)` and collect only the matching suffix as
`(ordinal, timestamp)` pairs. Sorting those pairs preserves insertion order even
for equal or backwards wall-clock timestamps. Empty legacy scopes, arbitrary
historical cutoffs and future timestamps retain their previous semantics.
There is no subtraction from an untrusted timestamp and no expiration watermark.

For S scopes, N records in a scope and K matches, lookup costs approximately
O(log S + log N + K log K), with O(K) temporary pairs. Insertion allocates a
logarithmic index entry. Retained index memory is still O(all sends): removing
unused record copies reduces duplication, not asymptotic growth. Query scratch
now carries timestamps as well as ordinals; large windows still require memory
and sorting. This is not a bounded cache, checkpoint or measured production SLO.

`clear()` resets the map and ordinal counter during the existing recovery reset;
replayed pushes rebuild the same projection. Failed insertion removes only a
newly created empty scope and never advances the ordinal. Ordinal overflow is
rejected. Existing coordinator error/recovery handling remains responsible for
an insertion exception after a durable attempt; no new retry path is introduced.
The journal and coordinator retain all durable identities, including old sends.

## Implemented OMS latency fields

`OmsJournalHealthSnapshot` supplies three `OmsLatencySummary` values through
both Execution daemons' existing five-second `heptatrader.oms-capacity.v1` stream:

| JSON object | Measured boundary | Included | Excluded |
|---|---|---|---|
| `append_latency` | Append entry to exit | journal lock wait, validation, serialization, enqueue/synchronous write, failed attempts | later async-worker completion and Broker I/O |
| `data_sync_latency` | each journal-file `fdatasync` call | creation, critical-write, replay, close and EINTR retries, including failures | directory `fsync`, Broker acknowledgement and unattempted calls |
| `replay_validation_latency` | Replay entry through full snapshot validation | lock wait, draining queued writes, sync, bounded parsing and identity validation | recovery callbacks, application projection/reconciliation and complete service readiness |

Every object contains `samples`, `total_ns`, `max_ns`, `last_ns`, `saturated`,
and fixed noncumulative histogram buckets. Durations use `steady_clock` and
nanoseconds. Counters belong to one journal object/process instance, not the
persistent ledger. Zero samples mean no completed observation, not zero latency.
Counters saturate rather than wrap; saturated values cannot support means or
rates. [Operational reporting](oms-operational-report.md) defines bucket upper-
bound quantiles, epoch isolation and classic histogram export.

Failed attempts are measured too. A sample is not a durability receipt. No
attempted sync means no sync sample; failed sync does not clear writer poison,
manufacture known capacity or permit a retry around recovery. Close-time
observations are not guaranteed to be exported before the process exits.

## Concurrency, privacy and compatibility

Instrumentation uses the existing journal mutex without a new worker or lock
hierarchy. Append's timer finishes under that mutex. Replay finishes validation
measurement before unlocking and invoking callbacks; callback health reads stay
reentrant and callback exceptions still propagate. Instrumentation adds no file
or network I/O and does not change journal schema or durable write order.

Messages contain numeric summaries, not command IDs, accounts, tokens or journal
payload. `authorization_effect=NONE` is unchanged. Observation fields may be
additive; authority-bearing wire protocols retain their stricter contracts.
Decoded bytes, not gzip file size, determine recovery capacity. Entry admission
and exit behavior are specified in [recovery capacity](oms-recovery-capacity.md).

## Executable evidence and limits

The existing core Python partition compiles the actual index vectors. They
compare the former vector semantics for strict bounds, scope collisions,
retrograde timestamps, clear/rebuild and randomized history. A non-copyable
record with a large payload is destroyed after insertion; query results remain
valid, proving the index neither retains that payload nor references its owner.
Coordinator tests remain the integration, idempotency and restart evidence.

Native OMS tests cover real writes, concurrent health reads, all-or-nothing
budget rejection, continued exit evidence, higher-budget recovery and a process
killed after durable append. The Python fault test links the real journal with
a test-only `fdatasync` failure, not a production fault knob. CMake fixture tests
are not production daemon acceptance; the separately built install test retains
that responsibility. No workflow or approval gate is added by this optimization.

Bounded software tests do not establish multiday stability, target-host memory/
recovery SLOs, arbitrary historical compatibility, all-runtime telemetry, actual
notification delivery or IB PAPER qualification. See [bounded acceptance](bounded-runtime-acceptance.md),
[persistence support](persistence-support-window.md) and the [metric inventory](../OBSERVABILITY-METRICS.md).

## Coordinator operations and complete local recovery

`ExecutionCoordinator::RuntimeObservation` copies fixed counters and O(1)
container sizes under its existing mutex. It never scans historical commands to
publish telemetry. Place, cancel and authoritative flatten record one of five
fixed outcomes (accepted, rejected, duplicate, uncertain, escaping exception).
The operation histogram starts after acquiring the coordinator lock and ends
before releasing it; it includes local durability and venue callback work but
excludes lock wait, outer policy/preview validation, socket delivery and later
Broker callbacks. An exception is counted and rethrown, not made successful.
Planless flatten and policy-layer early returns are not coordinator operations.

Recovery latency wraps the complete `RecoverFromJournal` execution, including
validation, projection and failure cleanup, after its lock acquisition. It is
not just journal parser timing; neither is it end-to-end Broker reconciliation.
Attempts returning false are measured. Counters are process-instance data, not
reconstructed from history, and reset on process restart; projections still
recover their original command identities. Saturating counters/histograms are
explicitly marked and never wrapped to apparent zero.

Both Execution daemons publish the additive `execution_metrics` object in their
existing five-second `heptatrader.oms-capacity.v1` observations. It has three
`results` rows (place, cancel, flatten), each with the five outcomes above;
`place_latency`, `cancel_latency`, `flatten_latency`, `recovery_latency` use the
same nanosecond histogram format. `retained_commands`, `order_owners`,
`fenced_owners`, `recovery_only_owners`, `retained_send_attempts` are counts, and
`mutation_blocked` is a boolean. The record contains no identifiers or secrets.
Journal and coordinator snapshots are obtained separately: do not infer a joint
transactional snapshot or an exact invariant spanning both objects.

The installed report validates fixed inventories, integer/boolean types and
result-to-latency accounting. It emits 15 `hepta_execution_commands_total`
series (three fixed operation labels, five fixed result labels), four latency
histograms in seconds, the count gauges, and explicit presence/saturation/block
flags. Missing old-artifact metrics have presence zero and omit all value series;
saturated counters or histograms are omitted. Saturation is an explicit report
alert. A deliberate terminal or maintenance block is not automatically paged:
the block gauge needs operational context; existing writer incidents keep their
separate severity. No additional collector daemon or listener is required.
Native operation/refusal/uncertainty/exception tests, compiled C++ serializer
vectors, hostile Python report tests and actual installed-daemon restart/report
acceptance exercise these paths. Source fixtures do not certify a target host,
a universal recovery SLO, bounded lifetime storage or delivered notifications.
