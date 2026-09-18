# OMS recovery capacity and read-only diagnostics

Status: CURRENT
Applies to: OmsJournal replay, generation-backed recovery, new-entry admission and offline diagnostics

## Recovery modes

HeptaTrader has two explicit coordinator recovery modes plus one simulator-only complete economic-state replay. They share the journal schema, command identity and fail-closed semantics, but they have deliberately different resource costs.

### Legacy / no-generation recovery

When no generation store exists, `OmsJournal::Replay` validates the complete pinned journal before the first recovery callback. It bounds decoded bytes, record count and record size, materializes the validated snapshot, and then projects it. A valid prefix of a corrupt, torn, changed or over-budget file is never applied.

| Deployment environment | Default | Accepted range | Unit |
|---|---:|---:|---|
| HEPTA_OMS_REPLAY_MAX_BYTES | 67108864 | 1–1073741824 | complete decoded snapshot bytes, including newlines |
| HEPTA_OMS_REPLAY_MAX_RECORDS | 65536 | 1–1000000 | all records, including duplicate command history |
| HEPTA_OMS_REPLAY_MAX_RECORD_BYTES | 262144 | 1–1048576 | one JSON record excluding its newline |

Equality is accepted; one over is rejected. Empty, zero, signed, whitespace, nondecimal and overflow settings fail initialization. Settings are trusted deployment inputs, never Agent claims. Gzip storage does not evade decoded limits.

### Generation-backed coordinator recovery

Stopped-state V1/V2 generation maintenance preserves all durable history while changing coordinator restart cost. The selected generation is authenticated by `CURRENT`, `CURRENT.runtime`, manifest digests, private-file identity and, for V2, parent-manifest lineage plus an active-tail sentinel. Native startup validates that authority, keeps cumulative command and send-attempt indexes descriptor-pinned, replays the bounded `hot-replay.jsonl` plus bytes after the active-tail sentinel, and services old command identities from disk on demand.

V2 seals only the event delta since its parent. Command identity is never expired. Immutable segments and cumulative indexes remain retained on disk. A damaged current generation, pointer disagreement, lineage/sentinel mismatch or index drift is a recovery failure; startup never silently falls back to a parent or treats the store as absent.

The coordinator keeps only current/hot commands plus a bounded historical lookup cache. This closes the former requirement to repopulate the entire permanent command universe into memory on every coordinator restart. It does not make disk history finite or eliminate every O(history) operation.

### Simulator economic checkpoint plus active-tail replay

The deterministic simulator needs terminal fills, admitted-order count and the
order-ID high watermark as economic/risk base state. A V2 seal therefore projects
one digest-bound simulator checkpoint into the bounded generation hot replay:
the maximum order ID, cumulative admitted-order count and non-zero instrument
positions are written as explicit checkpoint records followed by a ready marker.
The checkpoint is derived while all writers are stopped from the previously
verified checkpoint (or, for a pre-checkpoint lineage, one verified historical
reconstruction) plus the newly sealed delta.

On normal V2 startup, `ExecutionServiceRuntimeComposition` uses
`OmsGenerationStore::Recover` and restores the simulator from that checkpoint,
then applies only post-checkpoint active-tail place/fill events. Startup therefore
does not materialize maps for every historical admitted order and fill. A malformed,
duplicate, incomplete or regressing checkpoint fails closed. Legacy/V1 recovery
retains the original full-ledger semantics, and a V2 lineage without a valid
checkpoint is rejected rather than treated as empty history.

The stopped-state maintenance operation may still pay O(retained history) once
when upgrading an older lineage that predates simulator checkpoints. Subsequent
seals carry the checkpoint forward incrementally. Installed-process acceptance
exercises fill -> stop -> V2 seal -> restart, position, original command identity,
duplicate no-resend and a strictly newer order ID.

## New-entry pause before recovery capacity is exhausted

`ExecutionCoordinator::PlaceOrder` evaluates trusted journal health after same-ID replay/conflict and authority checks but before writing a new intent. `execution/new_entry_capacity.h` defines the arithmetic contract:

- decoded written bytes plus pending bytes must be below `max_bytes - max_bytes/5`;
- written records plus queued and buffered records must be below `max_records - max_records/5`;
- unknown capacity of an opened journal rejects new entry until complete recovery has established a trusted capacity state.

Division is integer division, so equality is the existing rounded-up 80% pause boundary. Subtraction-based comparisons prevent overflow from manufacturing headroom. A capacity refusal returns `OMS_NEW_ENTRY_CAPACITY_EXHAUSTED` or `OMS_NEW_ENTRY_CAPACITY_UNKNOWN` before a new send, does not append a rejection, does not cache a never-admitted command ID and does not clear old identities.

Generation-aware coordinator startup adopts the validated active-tail capacity rather than charging sealed history against the active writer budget. This permits stopped-state sealing to bound active restart/write growth while preserving immutable historical evidence. Guarded cancel, authoritative flatten, callbacks and terminal evidence keep their existing durable-write rules and are not reclassified as new risk entries.

## Historical command lookup

The cumulative runtime command index is sorted by complete `(agent_id, session_id, command_id)` key. Native lookup uses a binary search, validates the complete decoded key and canonical request hash, and distinguishes `Missing`, `Found` and `Error`. A corrupt or substituted index is never interpreted as a missing command.

Loaded historical commands enter a bounded coordinator cache. Exact duplicate requests return durable state without another venue call; conflicting reuse remains `IDEMPOTENCY_KEY_CONFLICT`. Old identity lookup therefore remains durable without permanent in-memory growth.

## Historical send-attempt window cost

Current V2 generations declare `send_attempt_index_order=account-domain-time-v1`. Their cumulative `send-attempt-index.tsv` is globally sorted by encoded account, encoded execution domain, timestamp, journal sequence and request identity. Native lookup validates the pinned file, uses a file lower-bound search to enter the requested account/domain/time suffix, then reads only the relevant ordered window rather than scanning every sealed attempt for each ordinary rate-budget query.

Pre-change V1 or V2 generations without the order declaration remain readable through the conservative compatibility scanner. The next V2 seal migrates such a parent to the sorted format with bounded external-sort chunks and pairwise file merges; it does not materialize the entire old index in Python memory. Once the parent already declares the sorted order, subsequent seals stream-merge the immutable parent index with the newly sealed attempts.

A changed account/domain or a cutoff that moves backwards remains semantically valid because the lower-bound query is performed against the requested key/cutoff; the optimization does not reset the rolling budget. Active-tail attempts are merged separately and excluded from the sealed-history result by stable request identity so one send is never double counted across a generation cut.

Index validation failure fails closed at the existing rate guard; it never resets the rate budget to zero.

## Storage maintenance, index memory and generation-chain management

`scripts/hepta_oms_lifecycle.py seal --stopped-state` is an explicit stopped-writer operation. V2 publication order is: create and fsync immutable delta generation files, prepare and fsync the lineage-bound active tail, atomically replace the active journal and sync its parent directory, publish `CURRENT`, then publish digest-bound `CURRENT.runtime`. Crash points before/after each boundary are tested.

Verification is streaming. `runtime-command-index.tsv` and `send-attempt-index.tsv` are counted/validated line by line rather than converted into whole-file Python lists. Legacy send-index migration uses bounded 8,192-row sort chunks and bounded pairwise merges. These changes bound maintenance **working memory** with respect to cumulative index size, apart from bounded per-tail projection structures and the configured hot-replay limits.

The generation parent walk has no arbitrary 1,024-generation cutoff. It records visited generation names and fails closed on a cycle; downgrade export can therefore walk a longer valid lineage. This removes the previous artificial export ceiling, but it does not make an indefinitely long lineage free: verification/export/simulator complete replay still pay I/O proportional to the required lineage/history.

Current generation directories intentionally contain **cumulative command and send-attempt index snapshots** for direct current-generation lookup. Older generation directories are retained because the lineage binds their manifests and immutable event segments. Consequently, frequent sealing can duplicate cumulative index bytes across generations even though event segments themselves are delta-only. The current format has no automatic parent deletion, index garbage collection or generation rebase that would safely remove those bound ancestors. Operators must choose a maintenance cadence with this physical-storage cost in mind; deleting old generation directories or indexes manually is unsupported and can break lineage verification/downgrade.

A future storage-rebase format may reduce cumulative-index duplication, but it must preserve complete durable command identity, every possible send, explicit downgrade semantics and crash-safe authority before any old lineage can be retired. The present implementation prefers retained evidence over silent pruning.

### Lineage rebase and cumulative-index compaction

Frequent ordinary seals intentionally retain cumulative index snapshots in each
ancestor generation. To keep that correct format from becoming permanent
quadratic physical duplication, `hepta_oms_lifecycle.py rebase --stopped-state`
provides an explicit lossless compaction boundary. It first reconstructs and
validates the complete strict JSONL ledger, builds a fresh independent V2 root
generation with cumulative command/send indexes and the simulator economic
checkpoint, durably moves that root into the real store, rotates the active
journal to the new lineage marker, then publishes `CURRENT` and
`CURRENT.runtime`. Every intermediate crash boundary remains fail-closed.

`--prune-ancestors` is deliberately separate. Old selected ancestors are
removed only after the new root and active journal verify successfully. The
new root contains the complete exported ledger, so permanent command identity,
every possible send, exact downgrade export and historical evidence remain
available even after the old chain is deleted. Unexpected file types or unsafe
metadata stop pruning rather than widening deletion. Rebase is maintenance work,
not authorization, and must never run concurrently with a writer.


## Downgrade export

`scripts/hepta_oms_lifecycle.py export` reconstructs a complete strict JSONL ledger from the newest applicable V1 base, all subsequent V2 delta segments and the current tail. Export is create-only, fsync'd and validated before success. It has `authorization_effect=NONE` and never replaces the active journal automatically. An older runtime must receive this explicit export rather than silently reading a V2 tail as if it were a complete ledger.

Export has no fixed generation-count ceiling, but it remains proportional to the selected lineage and total exported history. A corrupt parent binding, cycle, segment or active-tail lineage fails the export. This is the explicit downgrade boundary: the tool does not invent a shortened history to satisfy an old reader.

Optional gzip archive maintenance remains a distinct lossless stopped-state operation. Compressed storage is not checkpointing and cannot substitute for generation lineage or decoded recovery validation.

## Terminal mutation universe boundary

Generation-backed recovery and historical command lookup are long-horizon mechanisms. The IB PAPER terminal mutation manifest is a separate qualification/finalization artifact. The active finalization source universe remains scoped to the exact fenced owner `(agent_id, session_id, account, execution_domain)`: generation-backed enumeration and hot coordinator records apply that same four-part subject before a command enters the terminal witness. Historical commands from older or foreign sessions on the same account/domain remain durably queryable in OMS and do not silently become current-owner mutation authority.

The manifest implementation also carries a compact partitioned-summary representation for reviewed compatibility/scale work, but the public terminal-fence path must preserve owner-session scoping. A compact digest is not permission to combine foreign sessions or expire command identity.

## Diagnostics and reasons

`OmsJournalHealthSnapshot` exposes configured limits, observed active snapshot bytes, validated record count and replay reasons including `OMS_REPLAY_VALIDATED`, budget/record/byte limits, invalid/torn records, snapshot changes, allocation failure and I/O/identity failure. These are not Agent RPCs.

```bash
python3 scripts/verify_oms_journal_replay.py \
  --journal /private/offline-copy/oms.jsonl --capacity-json
```

Pass matching `--max-bytes`, `--max-records` and `--max-record-bytes` for a nondefault legacy/full-ledger deployment. The verifier opens read-only/nonblocking/no-follow, requires a regular file, bounds input before parsing, rejects empty/torn records and checks identity/metadata at EOF. Use an authorized consistent copy; a live writer can invalidate the snapshot.

Generation integrity is verified by the lifecycle/checkpoint tooling and again by native startup. Source-side diagnostics never grant PAPER or LIVE authority.

## Recovery procedure

On legacy budget failure, keep new risk paused, retain the exact journal and lease/key/deployment identity, validate a trusted copy, and either adopt a measured supported budget or perform tested stopped-state generation maintenance. Never delete lines or expire command IDs to make a restart fit.

On generation integrity failure, preserve the generation store and active tail, stop mutation admission and diagnose the exact pointer/manifest/index/sentinel failure. Do not select an older parent manually unless performing an explicit independently reviewed recovery/migration procedure that preserves every later possible send.

A larger budget or a successful generation seal does not resolve uncertain broker sends. Those still require authoritative reconciliation.

## Acceptance

Native and Python tests cover inclusive/over-limit replay boundaries, bad settings, torn/oversized records, callback atomicity, new-entry pause, generation publication crash points, V1→V2 compatibility, parent/current/sentinel corruption, ancient same-ID duplicate/conflict, no second venue send, sorted send-attempt continuity/migration across a cut, streaming cumulative-index verification, a lineage longer than 1,024 generations plus cycle rejection, repeated generations and explicit downgrade export.

The opt-in installed process lane additionally executes the real simulator across fill → stop → V2 seal → restart and verifies economic position, command identity, duplicate no-resend and order-ID watermark. The recovery-growth fixture remains useful for measuring legacy full-ledger cost. Generation fixtures prove bounded coordinator hot restart and bounded maintenance working memory, not bounded total disk storage. None of these source fixtures is a target-host multiday soak, physical durability benchmark, Broker qualification campaign or proof that a chosen maintenance cadence satisfies an operational SLO.
