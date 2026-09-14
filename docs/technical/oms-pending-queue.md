# OMS in-memory buffering budget

Status: CURRENT
Applies to: OmsJournal buffering; no persistent-schema or record-retention change

Disk replay capacity and in-memory buffering are independent limits. The
existing disk byte/count/record budgets remain unchanged. A buffered or async
append must now fit BOTH of these trusted deployment settings:

| Environment setting | Default | Range | Unit |
|---|---:|---:|---|
| HEPTA_OMS_QUEUE_MAX_BYTES | 8388608 | 1–1073741824 | retained serialized payload bytes including newlines |
| HEPTA_OMS_QUEUE_MAX_RECORDS | 8192 | 1–1000000 | combined async deque plus buffered vector entries |

Malformed, signed, empty, zero and overflowing settings reject initialization
before creating a journal with `OMS_QUEUE_INVALID_BUDGET`. Small byte budgets
are allowed deliberately: a record that cannot fit is rejected; direct durable
critical writes remain possible. These bounds describe payload/count, NOT exact
process RSS or allocator overhead. Vector reservation is capped to the record
budget, rather than blindly reserving an arbitrary batch-size setting.

## Overflow, flushing and safety

An append that cannot be buffered returns false, increments a saturating
`queueCapacityRejections` count and leaves previously accepted entries intact.
It does not evict, truncate, fabricate a successful append or retry a mutation.
The existing caller retains responsibility for failure/fencing. A full queue
alone does not poison otherwise intact disk identity; actual I/O/identity
failures retain their existing poisoned-writer behavior.

Direct critical synchronous writes do not enter the pending buffer and are not
blocked by its budget. Their prior flush/order/fsync behavior is unchanged.
Existing critical-async compatibility mode also obeys queue bounds; it does not
gain a durability guarantee. Canonical trading runtime still requires critical
synchronous durability. No environment request from an Agent selects a budget.

A queued-to-buffered move does not double-count bytes. Each fully written line
removes exactly its serialized bytes plus newline from pending occupancy. On a
partial batch failure, only the successfully written prefix is removed; the
unwritten suffix remains owned by the journal and the existing poison fence
prevents further mutation. Replay drains the pending collections as before.

## Actual observable fields

Existing `heptatrader.oms-capacity.v1` observations add pending_bytes,
max_pending_bytes, max_pending_records and queue_capacity_rejections. Existing
bytes/headroom continue to refer to the written ledger; they are not silently
redefined as projected disk use. Pending records retain their prior meaning.
The installed reporter accepts prior observations with these fields absent,
but validates all four together when present. It reports a full queue and new
same-epoch rejection increments without fabricating a healthy missing value.

## Evidence and remaining lifecycle work

`tests/oms_queue_budget_cases.h`, included by the existing durability executable,
exercises actual record/byte boundaries, no-file-on-invalid-configuration,
accepted-record preservation, critical exit at full capacity and replay.
The ordinary core and both sanitizer lanes execute that same test binary.

This addresses bounded **pending memory**, not indefinite on-disk retention.
The total restart replay budget and all durable command identities remain.
Automatic rotation/compaction, durable index paging and general prior-schema
migration are NOT implemented by this change. Do not clear the journal or expire
IDs to obtain a green restart. Follow [recovery capacity](oms-recovery-capacity.md)
and [state support](persistence-support-window.md) for the remaining constraints.
