# Runtime cost observations and send-attempt indexing

Status: CURRENT
Applies to: coordinator secondary index and OMS process-instance observations
Implementation: `HeptaTrade/execution/send_attempt_time_index.h`, `HeptaTrade/oms_latency_observation.h`, `HeptaTrade/oms_journal.cpp`, `HeptaTrade/oms_capacity_observation.h`
Tests: `tests/python/test_send_attempt_time_index.py`, `tests/oms_runtime_observation_cases.h`, `tests/python/test_oms_observation_faults.py`

## Send-attempt query contract

`ExecutionCoordinator::GetPlaceSendAttemptTimes` retains its existing signature,
account/domain isolation, strict `timestamp > cutoff` rule and insertion-order
result. The coordinator mutex still serializes its reads, live place/flatten
append paths and recovery. The caller's persistent send-attempt identity set is
unchanged; the index grants no permission to resend a command.

The old vector scan visited every retained attempt. `SendAttemptTimeIndex`
retains those records and adds a map keyed by the pair `(account, domain)` and a
per-scope timestamp multimap. Queries use `upper_bound(cutoff)` and collect only
the matching suffix. The matching insertion indices are sorted to preserve the
old output order even for duplicate or backwards wall-clock timestamps. Empty
legacy scopes, arbitrary historical cutoffs and future timestamps keep their
previous semantics. There is no subtraction from an untrusted cutoff.

For S scopes, N records in a scope and K matches, lookup costs approximately
O(log S + log N + K log K), rather than O(all retained records). Insertion now
has a logarithmic allocation cost. This is a time/space tradeoff: the secondary
index uses additional O(N) memory and is not a bounded cache or a compactor.
Large historical queries can still be expensive. A narrow-window benchmark is
not proof of improved end-to-end order latency, lock-wait time or host capacity.

All historical records and command identity tombstones remain present. No
clock watermark evicts entries, so clock regression cannot silently lose rate
history. `clear()` resets both projections during the existing recovery reset;
replayed pushes rebuild them. Insertion rolls back its added record and newly
created scope if an allocation/copy fails. Existing coordinator error/recovery
handling remains responsible for an insertion exception after a durable send
attempt; this change does not create an additional retry path.

## Implemented OMS latency fields

`OmsJournalHealthSnapshot` adds three `OmsLatencySummary` values. The existing
five-second structured capacity messages of both Execution daemons include
these as additive objects in `heptatrader.oms-capacity.v1`:

| JSON object | Measurement boundary | Included | Excluded |
|---|---|---|---|
| `append_latency` | Append entry to exit, including acquiring the journal mutex | validation, serialization, enqueue or synchronous write; failed calls | later async-worker completion and Broker I/O |
| `data_sync_latency` | each journal-file `fdatasync` call, including EINTR retries | creation, critical-write, replay and close attempts, including failures | directory `fsync`, Broker acknowledgement and unattempted calls |
| `replay_validation_latency` | Replay entry through full snapshot validation | mutex wait, draining queued writes, data sync, bounded parsing and identity validation; failed validations | recovery callbacks, application projection/reconciliation and full service readiness |

Every object contains `samples`, `total_ns`, `max_ns`, `last_ns`, and
`saturated`. Durations use `steady_clock` and nanoseconds. Counters belong to
one journal object/process instance and are not written to the persistent
journal. `samples=0` means no completed observation, not a measured zero.
Counters saturate rather than wrap; a saturated total is unsuitable for mean
or rate calculation. Fixed noncumulative latency buckets and explicit upper bounds are also emitted.
See [operational report](oms-operational-report.md) for bucket/quantile meaning,
service-instance isolation and classic histogram export.

An attempt is measured regardless of success. A latency sample must never be
used as a successful durability receipt. Existing error, poisoned-writer and
replay reason fields remain necessary. A failure before a data-sync call does
not increment its sample count. A failed data-sync does not clear the poison
fence, manufacture known capacity or permit a retry around recovery.

## Concurrency, privacy and compatibility

Updates and snapshot reads use the existing journal mutex, not another worker,
queue or lock hierarchy. Append's timer is destroyed while that mutex is held.
Replay explicitly finishes its validation sample before unlocking and invoking
callbacks; callback health reads remain reentrant and callback exceptions still
propagate. The finished timer is inert after unlock. Instrumentation performs
no file/network I/O and changes neither durable write order nor on-disk schema.

Messages contain numeric summaries and no command, account, token or journal
payload. Existing capacity status, headroom and `authorization_effect=NONE` are
unchanged. Consumers should accept additive observation fields; authority-
bearing wire protocols are not changed. Close-time observations cannot be
assumed to have been exported after the process exits.

## Executable evidence and its limits

The index-specific C++ vectors are compiled by the core Python partition. They
compare the actual index with the former vector semantics for exact bounds,
scope collisions, duplicate/retrograde timestamps, clear/rebuild and randomized
histories. The unchanged coordinator tests remain the production-composition,
deduplication and restart tests. Python source workflow tests execute the actual
merged command block with inert dependencies; comments, no-ops, swallowed
failures and invalid shell syntax cannot substitute for executed controls.

The OMS durability test calls `hepta_observation_test::Run()` from
`oms_runtime_observation_cases.h`. It exercises actual files, concurrent health
reads, all-or-nothing over-budget replay, continued exit evidence, higher-budget
recovery and a forked process killed after a durable append. The Python fault
test links the real journal with a test-only `fdatasync` wrapper that returns
EIO; no runtime fault knob or production permission is added.

CMake behavior fixtures execute the actual install module with inert payloads
and are explicitly not production daemon acceptance. They replace the old
source-spelling assertions. The separate real built-runtime install test stays
in the install partition. No new workflow or approval gate is added.

These are bounded software tests. They do not establish production-host
latency/recovery SLOs, a multiday soak, arbitrary previous-version compatibility,
all-runtime telemetry, host alert delivery, or IB PAPER qualification. See
[bounded acceptance](bounded-runtime-acceptance.md),
[persistence support](persistence-support-window.md) and the
[metric inventory](../OBSERVABILITY-METRICS.md). Host acceptance and the existing
external blockers remain separate; PAPER/LIVE authority is unchanged.
