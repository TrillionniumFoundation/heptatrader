# OMS recovery capacity and read-only diagnostics

Status: CURRENT
Applies to: OmsJournal replay and the installed offline verifier

## Explicit bounds without losing mutation history

Replay still validates the entire pinned snapshot before invoking any recovery
callback. It does not apply a valid prefix of a corrupt, truncated or over-budget
journal. Parsed-event storage has explicit bounded vector growth; pending line
bytes are checked before appending the next chunk. Allocation failure while
materializing returns failure before callbacks. Callbacks remain outside the
journal mutex and may perform reentrant health reads.

| Deployment environment | Default | Accepted range | Unit |
|---|---:|---:|---|
| HEPTA_OMS_REPLAY_MAX_BYTES | 67108864 | 1–1073741824 | complete snapshot bytes, including newlines |
| HEPTA_OMS_REPLAY_MAX_RECORDS | 65536 | 1–1000000 | all records, including duplicate command history |
| HEPTA_OMS_REPLAY_MAX_RECORD_BYTES | 262144 | 1–1048576 | one JSON record excluding its newline |

Equality is accepted; one over is rejected. Empty, zero, signed, whitespace,
nondecimal and overflow settings fail Init before creating/opening the journal.
Settings are supplied by the service's trusted deployment environment, never an
Agent request. Defaults are conservative engineering budgets, not a measured
production-host SLO. Record count and bytes jointly bound materialization; they
do not express exact process RSS, allocator overhead or the async queue budget.

Append refuses an individual record larger than this reader's record budget.
The total replay byte/count budget does NOT stop all journal writes: guarded
exit/terminal evidence must not be disabled merely because a running service
has accumulated history. Operators must monitor headroom before restarting.
There is no automatic compaction, rotation, truncation, schema rewrite or
command-ID expiry. Raising a deployment budget requires measuring the target
host's memory/recovery time; it does not validate corrupt records.

## Diagnostics and reasons

`OmsJournalHealthSnapshot` exposes configured limits, observed snapshot bytes,
validated record count and a replay reason: OMS_REPLAY_VALIDATED,
OMS_REPLAY_INVALID_BUDGET, OMS_REPLAY_BYTE_LIMIT,
OMS_REPLAY_RECORD_COUNT_LIMIT, OMS_REPLAY_RECORD_BYTE_LIMIT,
OMS_REPLAY_INVALID_RECORD, OMS_REPLAY_TORN_RECORD,
OMS_REPLAY_SNAPSHOT_CHANGED, OMS_REPLAY_ALLOCATION_FAILURE, or
OMS_REPLAY_IO_OR_IDENTITY_FAILURE. This C++ health data is not a new Agent RPC.

The existing offline verifier can stream an identifier-free capacity summary:

```bash
python3 scripts/verify_oms_journal_replay.py \
  --journal /private/offline-copy/oms.jsonl --capacity-json
```

For a nondefault deployment, pass the matching `--max-bytes`, `--max-records`
and `--max-record-bytes`. The command opens read-only/nonblocking/no-follow,
requires a regular file, bounds input before parsing, rejects empty/torn records
and checks file/path identity and metadata at EOF. It never publishes a success
summary before full validation. `warning_at_80_percent` is a planning signal;
no order IDs, tokens or journal payload are printed in capacity mode. A live
writer can invalidate the snapshot: use an authorized offline consistent copy,
not repeated mutation retries. Empty journals have no diagnostic event summary
although the native replay accepts an empty initialized journal.

## Recovery procedure

On budget failure, retain the exact file, lease store, key and deployment
identity. Keep new risk fenced. Inspect the bounded summary and failure reason;
measure a trusted copy with the proposed larger budget under non-Broker tests.
Restart only after the chosen artifact/schema/budget pair is accepted on the
actual host. On corruption, uncertain sends or incomplete terminal witnesses,
use authoritative reconciliation rather than deleting the offending line.

`tests/oms_journal_durability_tests.cpp` covers inclusive/over-limit boundaries,
malformed configuration, split/oversized/torn records, many small events,
callback atomicity/reentrancy and history preservation. Existing coordinator
restart/idempotency tests still run. `test_oms_capacity.py` covers offline
bounds, special files, snapshot drift and non-mutating output. These tests are
not a multiday load test or proof that any production journal fits the defaults.
