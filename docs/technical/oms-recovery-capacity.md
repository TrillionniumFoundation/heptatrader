# OMS recovery capacity and read-only diagnostics

Status: CURRENT
Applies to: OmsJournal replay, generation-backed recovery, new-entry admission and offline diagnostics

## Recovery modes

HeptaTrader has two explicit coordinator recovery modes plus a simulator economic-checkpoint/tail projection. They share the journal schema, command identity and fail-closed semantics, but they have deliberately different resource costs.

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

### Simulator economic-state checkpoint and tail replay

The deterministic simulator has one additional requirement: terminal fills, admitted-order count and the order-ID high watermark are economic/risk base state, so coordinator command hot state alone is insufficient after V2 rotation. Each stopped-state V2 seal now carries a compact simulator checkpoint in the bounded hot replay: maximum order ID, cumulative admitted-order count and non-zero per-instrument positions, followed by an explicit ready marker. The checkpoint is derived from the previous verified checkpoint plus the newly sealed tail. Only stopped-state maintenance may reconstruct that state from an older V2 lineage; `hepta_oms_lifecycle.py seal --stopped-state` owns that one-time O(total retained history) migration.

`ExecutionServiceRuntimeComposition` restores the compact checkpoint through ordinary generation recovery and applies only post-checkpoint hot/tail simulator events. Runtime restart has no full-history V2 fallback and therefore no longer builds maps of every historical admitted/fill order. A selected V2 generation without the ready checkpoint fails closed with `EXECUTION_SIMULATOR_GENERATION_CHECKPOINT_REQUIRED` until stopped-state migration is performed; corrupt, partial or regressing checkpoint state also fails closed. The installed-process acceptance exercises real simulator fill → stop → V2 seal → restart, then checks position, original command identity, no-resend duplicate behavior and a strictly newer order ID; a second restart checks checkpoint plus active-tail composition.

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

Verification is streaming. `runtime-command-index.tsv` and `send-attempt-index.tsv` are counted/validated line by line rather than converted into whole-file Python lists. Native sequential generation-index consumers now use an exact-offset 64 KiB buffered reader after any random lower-bound step, so a linear scan does not reread overlapping 64 KiB windows for every row. Legacy send-index migration uses bounded 8,192-row sort chunks and bounded pairwise merges. These changes bound maintenance **working memory** with respect to cumulative index size, apart from bounded per-tail projection structures and the configured hot-replay limits.

The generation parent walk has no arbitrary 1,024-generation cutoff. It records visited generation names and fails closed on a cycle; downgrade export can therefore walk a longer valid lineage. This removes the previous artificial export ceiling, but it does not make an indefinitely long lineage free: verification/export and stopped-state migration or rebase still pay I/O proportional to the required lineage/history.

Current generation directories intentionally contain **cumulative command and send-attempt index snapshots** for direct current-generation lookup. Between maintenance rebases, frequent sealing therefore duplicates cumulative index bytes even though event segments are delta-only.

`hepta_oms_lifecycle.py rebase --stopped-state` is the explicit compaction boundary. It first seals the active tail using the ordinary crash-safe path, verifies the selected lineage, streams the complete logical event ledger into one new parentless V2 segment, copies the verified cumulative command/send indexes and bounded hot replay, and publishes a new digest-bound CURRENT/tail pair. With `--prune-ancestors`, old lineage directories are deleted only after the new generation has been published and re-verified; each directory and file is rechecked as a private regular object before unlink. A crash before publication leaves the old authority intact or fails closed at the existing tail/current boundary; pruning occurs only after the new parentless generation is authoritative.

Rebase does not expire command identity, discard event history or manufacture a shortened downgrade ledger. It trades an explicit stopped-state O(total retained history) maintenance pass for bounded lineage depth and removal of cumulative-index duplication. Operators should trigger it by measured storage/generation thresholds rather than on every seal.

## Downgrade export

`scripts/hepta_oms_lifecycle.py export` reconstructs a complete strict JSONL ledger from the newest applicable V1 base, all subsequent V2 delta segments and the current tail. Export is create-only, fsync'd and validated before success. It has `authorization_effect=NONE` and never replaces the active journal automatically. An older runtime must receive this explicit export rather than silently reading a V2 tail as if it were a complete ledger.

Export has no fixed generation-count ceiling, but it remains proportional to the selected lineage and total exported history. A corrupt parent binding, cycle, segment or active-tail lineage fails the export. This is the explicit downgrade boundary: the tool does not invent a shortened history to satisfy an old reader.

Optional gzip archive maintenance remains a distinct lossless stopped-state operation. Compressed storage is not checkpointing and cannot substitute for generation lineage or decoded recovery validation.

## Terminal mutation universe boundary

Generation-backed recovery and historical command lookup are long-horizon mechanisms. The IB PAPER terminal mutation manifest is a separate qualification/finalization artifact. The active finalization source universe remains scoped to the exact fenced owner `(agent_id, session_id, account, execution_domain)`: generation-backed enumeration and hot coordinator records apply that same four-part subject before a command enters the terminal witness. Historical commands from older or foreign sessions on the same account/domain remain durably queryable in OMS and do not silently become current-owner mutation authority.

Generation-backed public finalization lower-bounds the sorted cumulative command index to the exact encoded agent/session prefix and streams only that sealed owner/session range into the history summary. Account/domain and durable-intent checks still select the exact campaign subject, the pinned index is revalidated after the scan, and only matching hot/active-tail mutation records absent from the sealed index are materialized. Any mismatch between a hot record and its sealed command identity fails closed. The fixed-size HPM2 partition binding combines the sealed summary with that bounded tail, so the old per-enumeration record ceiling is not the public generation-backed finalization limit.

The manifest implementation preserves exact owner-session scoping throughout this compact path. A compact digest is not permission to combine foreign sessions, accept an index conflict or expire command identity.

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

The opt-in installed process lane additionally executes the real simulator across fill → stop → V2 seal → restart and verifies economic position, command identity, duplicate no-resend and order-ID watermark. The recovery-growth fixture remains useful for measuring legacy full-ledger cost. Generation fixtures prove bounded coordinator hot restart and bounded maintenance working memory, not bounded total disk storage. The source suite records a synthetic 16-generation cost curve with 1,024 commands / 4,096 logical events, checkpoints at 256/512/1,024 commands and 4/8/16 decoded owner/session identities, plus seal/verify time, current cumulative command/send-index bytes, generation output bytes, logical event bytes, retained bytes, retained-to-logical storage amplification and rebase time. Its `test_process_peak_rss_kib` field is only a whole-Python-test-process diagnostic because `ru_maxrss` may include earlier allocations; it is not stage-isolated maintenance-memory evidence. The isolated installed-process cost curve is the runtime memory evidence: it exercises 8, 40 and 168 cumulative admitted orders and records coordinator recovery, simulator-state recovery, complete startup-ready latency, inclusive place-operation p99 bucket/max, journal bytes, retained generation bytes and per-process Linux `VmHWM`, plus a post-rebase restart. The generation-index reader regression separately proves linear selected-range read bytes on a 20,000-row fixture, while the native terminal fixture crosses 4,202 terminal commands. These measurements are descriptive evidence from the exact CI host, not universal fixed performance thresholds. None of these source fixtures is a target-host multiday soak, physical durability benchmark, Broker qualification campaign or proof that a chosen maintenance cadence satisfies an operational SLO.

## Opt-in scaled generation and maintenance observation

`hepta_execution_coordinator_tests --generation-growth N` accepts 1–100,000
synthetic placements in newly created temporary state, seals batches no larger
than 4,096 orders with the real lifecycle tool, and rebases/prunes only that
owned test lineage. Existing production replay/admission limits are unchanged.
Every stage execs a fresh test reader to measure recovery and process peak RSS
without inheriting writer/maintenance allocation peaks. It verifies empty hot
command state, ancient/new duplicate identity, conflict rejection and no resend
both before and after rebase. Maintenance records include time and retained disk
bytes. A 16-order version runs in the existing core target.

These records measure native coordinator persistence/recovery and stopped-state
maintenance on the executing host. They do not exercise actual Broker I/O,
production account data, the full installed daemon startup, target-host alert
delivery or a multiday soak. Their stage/source/host context must accompany any
performance interpretation; a finite successful probe is not an unbounded SLO.

### Explicit installed-process extended workload

The existing isolated generation test accepts `HEPTA_GENERATION_COST_PROFILE=extended`
only inside its already opt-in disposable Linux/multi-UID fixture. It performs
32/128/512 buy/sell pairs per stage: 64, 320 and 1,344 cumulative admitted orders,
then stopped-state seal/restart at each cut and rebase/restart. The default `core`
workload remains 8/40/168 orders; ordinary release acceptance does not silently
become an extended benchmark. Both workloads use the same digest-pinned installed
core artifact, separate service/Agent UIDs, real preview/submit/status IPC, enabled
finite rate limits, native final risk, journal and economic projection. No runtime
limit or account authority is disabled for this test.

The consumer independently selects the expected workload profile. Extended
receipts must retain all five starts of each installed daemon, stable executable
hashes, the expected service UIDs, enough measured placement samples per stage,
elapsed time and successful final orderly shutdown. A core receipt cannot promote
itself to extended scope; a changed executable, incomplete workload or forced
shutdown is not accepted. Receipt staging is validated before create-only durable
publication. Host-kernel, storage and CPU context should accompany the emitted
measurements. These are synthetic installed-daemon observations, not a multi-day
soak, physical power-loss test, actual operator alert delivery or Broker qualification.

### Opt-in installed capacity workload

The existing installed-process cost test also accepts
`HEPTA_GENERATION_COST_PROFILE=capacity`: 1,536 buy/sell pairs in each of its
three stages, or 3,072/6,144/9,216 cumulative real simulator admissions.
Each stage produces 21,504 ordinary journal records (seven per admission),
below the existing 80% pause boundary of the default 65,536-record tail budget.
The workload also stays below the current installed simulator
10,000-admission risk limit, whose count remains continuous across checkpoint
and restart. A proposed 21,504-admission measurement was correctly refused at
that limit; it did not qualify and must not be represented as a successful
capacity curve. No daily clock, counter, session or policy is reset to extend
the measurement. The existing byte pause boundary also remains enforced. This keeps maintenance
cadence bounded while increasing retained history; it does not enlarge the
writer budget to conceal a capacity refusal. The
ordinary `core` and `extended` profiles are unchanged. This is an opt-in
measurement, not an added routine merge gate. It uses the same installed owner,
preview, final risk, journal, fill, generation, restart and ancient-duplicate
path, with the existing supported operator-configured trade-rate limit. No
production limit is widened and no Broker transport is enabled.

The evidence consumer must explicitly select `capacity`; a core/extended receipt
cannot claim its scope. Like extended evidence it requires complete samples,
exact artifact/source identity, stable executable hashes, distinct process
observations, the expected non-root owners and verified orderly shutdown.
Successful source tests are not a capacity observation. Record actual measured
results separately with host/filesystem/resource limits; this test is not a
physical power-loss or multi-day target-host qualification.

Capacity mode preserves and counts an original uncertain placement reply. Before
another order it queries that same command ID through the ordinary authenticated
status tool and requires positive matching authoritative acceptance; it never
creates another preview, resends the mutation, extends its expiry or changes a
daemon timeout. Position and active-order observations still must complete.
Unresolved, rejected, mismatched or unqualified status fails the workload. The
receipt retains bounded uncertainty observations and requires exactly one sealed
send attempt per actual admission. Latency samples include the slow operations.
Core/extended behavior remains unchanged. All installed generation tests retain
bounded daemon logs and source/artifact-bound diagnostics after shutdown, even
on failure; diagnostic capture cannot publish an acceptance result.

## Choosing a maintenance cadence from existing measurements

Use the existing `--generation-growth N` diagnostic on the intended filesystem,
recording compiler/source identity, storage device, concurrent host load, all seal
and rebase stages, fresh-reader RSS/recovery and ancient duplicate/conflict checks.
Standard absolute `TMPDIR` selects only the owned synthetic fixture location.
The probe retains every sync and identity check; a volatile filesystem must be
labelled as such. An interrupted or timed-out run is a failed observation, not a
smaller successful curve. Installed extended/capacity workloads remain separate
whole-daemon acceptance and are not replaced by the coordinator probe.

Keep the current stopped-state generation design until measured operating limits
justify a storage change. Seal before the unchanged active-tail 80% admission
pause, based on observed event/byte growth and the available maintenance window.
Do not rebase after every seal: first compare cumulative retained bytes with the
logical ledger and current indexes, then choose a storage/lineage trigger and
verify its stop/restore cost. Reserve space for both the old lineage and the new
complete generation until publication and verification permit pruning. Rebase
can reduce retained bytes without reducing a particular observed recovery time.
Neither one fast run nor a host-specific threshold changes the runtime limits.

Lease acknowledgement capacity has a different lifecycle: the Supervisor's
2 MiB bound includes permanent anti-resurrection evidence and cannot be relieved
by deleting old acknowledgements. Its independent history-growth fixture and
capacity metrics must drive capacity planning; OMS rebase is not a lease-store
compactor. These decisions add no routine capacity campaign or approval gate.

## Desktop observations (2026-09-25)

The ordinary coordinator diagnostic completed 256, 1,024 and 8,192 synthetic
orders on the desktop ext4 filesystem. The diagnostic binary was built from
`3ea97731097d7a220951a2fc3a4d1e140934a436`, whose complete source tree
`537f10995e33bf2d62359cfdf42ad4720bf7a2bf` is identical to merged main
`b1ae6c9d05ae90bc69bd25b966b294ee70e78784`. Release GCC, ordinary syncs and
unchanged runtime limits were used. Every final sealed and rebased fresh-process
reader rejected ancient conflicting identities and produced zero resends.

| Orders | Sealed recovery (ms) | Rebased recovery (ms) | Rebased peak RSS (MiB) | Retained storage before / after rebase (MiB) | Rebase (s) |
|---:|---:|---:|---:|---:|---:|
| 256 | 4.80 | 7.23 | 7.49 | 1.76 / 1.26 | 0.301 |
| 1,024 | 11.75 | 21.45 | 7.62 | 7.02 / 5.04 | 0.650 |
| 8,192 | 72.59 | 149.14 | 7.75 | 56.34 / 40.47 | 3.165 |

These are single-run, warm-cache-uncontrolled observations on a shared host, not
a production SLO or a maximum supported history. They demonstrate that rebase
reduced retained bytes in this workload but increased the measured fresh-process
recovery interval; deleting history or raising limits is not justified. The
fixture uses a synthetic venue callback, not installed multi-UID daemons or IB.
The installed capacity workload, multi-day operation and physical power-loss
qualification remain separate. No Broker mutation or trading authorization was
performed by these measurements.

The same desktop native suite exercised lease stores with 1, 8 and 88 retained
acknowledgement groups (23,572, 188,058 and 2,071,018 encoded bytes). Observed
reopen times were 1.77, 13.32 and 147.21 ms. Every case retained the oldest
acknowledgement, rejected retired-owner reuse after reopen and completed the
existing fence/remove checks; the near-full case refused growing admission.
These bounded fixtures do not establish indefinite acknowledgement capacity.
