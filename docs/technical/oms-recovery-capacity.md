# OMS recovery capacity and bounded historical lookup

Status: CURRENT
Applies to: legacy replay, generation-backed startup, new-entry admission and offline diagnostics

## Two supported recovery modes

HeptaTrader has one OMS authority with two startup forms. **Legacy/no-generation**
startup validates and materializes the complete pinned JSONL snapshot before the
first projection callback. **Generation-backed** startup verifies `CURRENT`, the
selected immutable generation, parent lineage, cumulative indexes and the active
journal sentinel, then projects only bounded hot replay plus the active tail.
Neither form applies a valid prefix of later-corrupt evidence, expires a command
identity or resends a possible mutation.

The legacy decoded replay budgets remain:

| Deployment environment | Default | Accepted range | Unit |
|---|---:|---:|---|
| `HEPTA_OMS_REPLAY_MAX_BYTES` | 67108864 | 1–1073741824 | decoded recovery bytes |
| `HEPTA_OMS_REPLAY_MAX_RECORDS` | 65536 | 1–1000000 | replayed records |
| `HEPTA_OMS_REPLAY_MAX_RECORD_BYTES` | 262144 | 1–1048576 | one JSON record excluding newline |

Equality is accepted; one over is rejected. Empty, zero, signed, whitespace,
nondecimal and overflow settings fail initialization. Gzip storage never evades
decoded limits. These are engineering budgets, not measured target-host SLOs.

For generation-backed startup the same byte/record limits bound the selected hot
replay plus active tail. Immutable sealed history is verified through digest-bound
manifests and pinned indexes instead of being reconstructed into the coordinator.
Historical `(agent, session, command)` status/admission lookup is binary-search
based and materializes only a bounded cache entry.

## New-entry pause

`ExecutionCoordinator::PlaceOrder` evaluates trusted journal health after same-ID
replay/conflict and authority checks but before writing a new intent. New entry is
paused at the rounded-up 80 percent byte/record boundary. Unknown capacity also
rejects new entry. A capacity refusal writes no mutation record, caches no new ID,
sets no global mutation block and never removes history. Exact old-command replay,
cancel and separately guarded authoritative flatten remain available according to
their existing safety contracts.

The pause is headroom protection for the active writer, not a reservation for every
future callback. Exit/callback evidence can still grow the active tail after the
observation. Operators must seal/maintain history before a restart would exceed the
configured hot-recovery budget.

## V2 stopped-state sealing

`scripts/hepta_oms_lifecycle.py seal` runs only with the writer stopped. V2 writes an
immutable delta segment, cumulative permanent command/send indexes, bounded hot
replay and a parent-digest-bound runtime manifest. It then atomically replaces the
active ledger with a lineage sentinel and advances `CURRENT`/`CURRENT.runtime` in a
crash-tested order. A crash before publication leaves the previous complete ledger;
pointer/sentinel disagreement after publication fails closed.

New V2 send-attempt indexes are globally ordered by encoded account, execution
domain, timestamp, durable sequence and request identity. Rolling rate-window
lookup therefore uses a file lower bound plus the matching suffix rather than a
full permanent-history scan. Legacy V2 generations without the ordering declaration
remain readable through the conservative compatibility scan; the next successful
seal writes the current sorted format.

Permanent command IDs are never expired. Immutable generation segments remain until
an explicit external retention policy exists. `export` reconstructs a strict
create-only complete JSONL ledger for an older full-ledger reader; downgrade is an
operator action, never an automatic fallback.

## Terminal history binding

Terminal shutdown must prove all possible mutations are represented without making
lifetime history an in-memory vector. Generation-backed terminalization streams the
pinned permanent command index into fixed-size count/digest bindings and adds only
active-tail commands that are not already sealed. HPM2 persists those bindings,
finalization identity and counts in a small manifest; it does not serialize one
`command=`/`correlation=` line per historical mutation. The old HPM1 manifest remains
readable for already persisted evidence.

HPM2 removes the former 4,096-command/correlation terminal-manifest ceiling. The
sealed correlation digest is explicitly a versioned correlation-reference binding
in permanent-command order; active-tail unique correlations are bound as a separate
partition before the final HPM2 digest is produced. Receipts and latches bind the
resulting digest/count tuple and do not infer economic state from it. Final flatness,
zero unresolved commands, complete Broker barriers and drained callbacks remain
separate mandatory terminal evidence.

## Diagnostics and failure semantics

`OmsJournalHealthSnapshot` reports configured limits, validated bytes/records,
pending occupancy and replay reason. The offline command remains read-only:

```bash
python3 scripts/verify_oms_journal_replay.py \
  --journal /private/offline-copy/oms.jsonl --capacity-json
```

Use a private consistent copy and the deployment's effective limits. Middle-file
corruption, torn records, unsafe paths, snapshot replacement, generation digest or
lineage drift, index corruption and allocation/I/O failure all fail closed. A larger
budget cannot validate corruption or resolve an uncertain send.

## Acceptance

Native and Python tests cover inclusive/over-limit replay budgets, oversized/torn
records, path substitution, capacity admission, old-ID duplicate/conflict, repeated
V2 generations, publication crash points, index corruption, V1→V2 transition,
explicit downgrade export, retrograde timestamps, sorted send-window lookup and
HPM2 terminal history above the former 4,096-record ceiling. The synthetic recovery
growth probe remains useful for measuring process RSS/recovery time, but it is a
measurement tool rather than the persistence design itself.

Target-host multiday stability, filesystem durability characteristics, operator
alert delivery and actual Broker qualification remain external evidence. They do
not reopen repository `OMS-LIFECYCLE-002`; they are tracked as host/operations and
qualification scope.
