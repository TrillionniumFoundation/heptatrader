# OMS recovery capacity and read-only diagnostics

Status: CURRENT
Applies to: OmsJournal replay, new-entry admission and the installed offline verifier

## Explicit bounds without losing mutation history

Replay validates the entire pinned snapshot before invoking any recovery
callback. It does not apply a valid prefix of a corrupt, truncated or over-budget
journal. Parsed-event storage has explicit bounded vector growth; pending line
bytes are checked before appending the next chunk. Allocation failure while
materializing returns failure before callbacks. Callbacks remain outside the
journal mutex and may perform reentrant health reads.

| Deployment environment | Default | Accepted range | Unit |
|---|---:|---:|---|
| HEPTA_OMS_REPLAY_MAX_BYTES | 67108864 | 1–1073741824 | complete decoded snapshot bytes, including newlines |
| HEPTA_OMS_REPLAY_MAX_RECORDS | 65536 | 1–1000000 | all records, including duplicate command history |
| HEPTA_OMS_REPLAY_MAX_RECORD_BYTES | 262144 | 1–1048576 | one JSON record excluding its newline |

Equality is accepted; one over is rejected. Empty, zero, signed, whitespace,
nondecimal and overflow settings fail Init before creating/opening the journal.
Settings are supplied by the service's trusted deployment environment, never an
Agent request. Defaults are conservative engineering budgets, not measured
production SLOs. Counts/bytes bound materialization, not exact RSS, allocator
overhead or the async queue budget. Gzip does not evade decoded limits.

Append refuses an individual record larger than the reader's record budget.
Total replay byte/count limits do not stop all writes: guarded exit, callback
and terminal evidence must remain writable. There is no automatic rotation,
truncation, semantic compaction or command-ID expiry. Optional stopped-state
[storage repacking](oms-archive-lifecycle.md) preserves every event and does not
reduce decoded history or materialized identities.

## New-entry pause before recovery capacity is exhausted

`ExecutionCoordinator::PlaceOrder` now evaluates trusted journal health after
same-ID replay/conflict and authority checks, but before writing a new intent.
`execution/new_entry_capacity.h` defines the small arithmetic contract:

- decoded written bytes plus pending bytes must be below `max_bytes - max_bytes/5`;
- written records plus queued and buffered records must be below
  `max_records - max_records/5`;
- unknown capacity of an opened journal rejects new entry until complete replay.

Division is integer division, so this is the existing rounded-up 80% warning
boundary. Equality pauses new entry. Subtraction-based comparisons prevent
overflow from turning excessive counts into apparent headroom. Gzip storage
bytes and Agent claims of reduction never substitute for those observations.

A capacity refusal returns `OMS_NEW_ENTRY_CAPACITY_EXHAUSTED` or
`OMS_NEW_ENTRY_CAPACITY_UNKNOWN` before any new send. It does not append a
rejection, cache a new command ID, set the global mutation block or remove an
old identity. Thus repeated new rejected IDs do not consume retained history.
Status lookup for such a never-admitted command remains absent. A caller may
retry the same normalized intent/ID after verified maintenance; it must still
not replace the identity of a possibly sent command.

Previously recorded commands are resolved before the capacity check, so exact
replay stays available and conflicting reuse stays rejected. Cancel and the
separately guarded authoritative-flatten operations do not pass through this
new-entry gate. Their existing ownership, fencing, risk and durable-write
requirements are unchanged. A missing or poisoned writer still follows the
existing hard journal-write failure and global block, not a soft capacity pause.

This is an early admission mitigation, NOT a reserved quota for every possible
future callback, a guarantee that all exits fit, or an unlimited retention
solution. Concurrent callbacks and exits can still grow history after the
observation and beyond the replay limit. Operators must monitor the remaining
headroom and finish maintenance before restart. Permanent coordinator identity
state and full-history replay still grow; `OMS-LIFECYCLE-002` remains open.

## Diagnostics and reasons

`OmsJournalHealthSnapshot` exposes configured limits, observed snapshot bytes,
validated record count and replay reasons: OMS_REPLAY_VALIDATED,
OMS_REPLAY_INVALID_BUDGET, OMS_REPLAY_BYTE_LIMIT,
OMS_REPLAY_RECORD_COUNT_LIMIT, OMS_REPLAY_RECORD_BYTE_LIMIT,
OMS_REPLAY_INVALID_RECORD, OMS_REPLAY_TORN_RECORD,
OMS_REPLAY_SNAPSHOT_CHANGED, OMS_REPLAY_ALLOCATION_FAILURE, or
OMS_REPLAY_IO_OR_IDENTITY_FAILURE. These are not new Agent RPCs.

```bash
python3 scripts/verify_oms_journal_replay.py \
  --journal /private/offline-copy/oms.jsonl --capacity-json
```

Pass matching `--max-bytes`, `--max-records` and `--max-record-bytes` for a
nondefault deployment. The command opens read-only/nonblocking/no-follow,
requires a regular file, bounds input before parsing, rejects empty/torn records
and checks identity/metadata at EOF. It never reports success before full
validation. Capacity output contains no IDs, tokens or journal payload. Use an
authorized offline consistent copy; a live writer can invalidate the snapshot.
Empty journals have no diagnostic event summary although native replay accepts
an empty initialized journal. The [online report](oms-operational-report.md)
adds fixed numeric telemetry and safe publication, not admission authority.

## Recovery procedure and acceptance

Retain exact journal, lease store, key and deployment identities. On budget
failure keep new risk paused, measure a trusted copy with a proposed supported
budget, and restart only after that artifact/schema/budget pair has been
accepted on the actual host. A larger budget does not validate corruption or
resolve uncertain sends; use authoritative reconciliation, never delete lines.

Native durability tests cover inclusive/over-limit boundaries, bad settings,
split/oversized/torn records, callback atomicity/reentrancy and history retention.
`tests/venue_placement_cases.h`, invoked by the actual coordinator suite, tests
arithmetic/overflow, no-send/no-cache refusals, duplicate/conflict behavior,
owned cancel and authoritative flatten at capacity, and closing/reopening the
journal with unchanged command identities. Existing journal-write failure and
exit-risk tests remain required. `test_oms_capacity.py` covers offline bounds,
special files, snapshot drift and non-mutating diagnostics. These tests are not
a multiday soak or proof that a production ledger fits the defaults.
