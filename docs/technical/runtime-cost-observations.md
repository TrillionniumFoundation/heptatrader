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
