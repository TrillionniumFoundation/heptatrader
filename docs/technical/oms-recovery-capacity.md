# OMS recovery capacity and read-only diagnostics

Status: CURRENT
Applies to: OmsJournal replay, generation-backed recovery, new-entry admission and offline diagnostics

## Recovery modes

HeptaTrader now has two explicit recovery modes. They share the same journal schema, command identity and fail-closed semantics, but they have different resource costs.

### Legacy / no-generation recovery

When no generation store exists, `OmsJournal::Replay` validates the complete pinned journal before the first recovery callback. It bounds decoded bytes, record count and record size, materializes the validated snapshot, and then projects it. A valid prefix of a corrupt, torn, changed or over-budget file is never applied.

| Deployment environment | Default | Accepted range | Unit |
|---|---:|---:|---|
| HEPTA_OMS_REPLAY_MAX_BYTES | 67108864 | 1–1073741824 | complete decoded snapshot bytes, including newlines |
| HEPTA_OMS_REPLAY_MAX_RECORDS | 65536 | 1–1000000 | all records, including duplicate command history |
| HEPTA_OMS_REPLAY_MAX_RECORD_BYTES | 262144 | 1–1048576 | one JSON record excluding its newline |

Equality is accepted; one over is rejected. Empty, zero, signed, whitespace, nondecimal and overflow settings fail initialization. Settings are trusted deployment inputs, never Agent claims. Gzip storage does not evade decoded limits.

### Generation-backed recovery

Stopped-state V1/V2 generation maintenance preserves all durable history while changing restart cost. The selected generation is authenticated by `CURRENT`, `CURRENT.runtime`, manifest digests, private-file identity and, for V2, parent-manifest lineage plus an active-tail sentinel. Native startup validates that authority, keeps cumulative command and send-attempt indexes descriptor-pinned, replays the bounded `hot-replay.jsonl` plus bytes after the active-tail sentinel, and services old command identities from disk on demand.

V2 seals only the delta since its parent. Command identity is never expired. Immutable segments and cumulative indexes remain retained on disk until an explicit external retention policy exists. A damaged current generation, pointer disagreement, lineage/sentinel mismatch or index drift is a recovery failure; startup never silently falls back to a parent or treats the store as absent.

The coordinator keeps only current/hot commands plus a bounded historical lookup cache. This closes the former requirement to repopulate the entire permanent command universe into memory on every restart. It does not make disk history finite or eliminate all O(history) administrative operations.

## New-entry pause before recovery capacity is exhausted

`ExecutionCoordinator::PlaceOrder` evaluates trusted journal health after same-ID replay/conflict and authority checks but before writing a new intent. `execution/new_entry_capacity.h` defines the arithmetic contract:

- decoded written bytes plus pending bytes must be below `max_bytes - max_bytes/5`;
- written records plus queued and buffered records must be below `max_records - max_records/5`;
- unknown capacity of an opened journal rejects new entry until complete recovery has established a trusted capacity state.

Division is integer division, so equality is the existing rounded-up 80% pause boundary. Subtraction-based comparisons prevent overflow from manufacturing headroom. A capacity refusal returns `OMS_NEW_ENTRY_CAPACITY_EXHAUSTED` or `OMS_NEW_ENTRY_CAPACITY_UNKNOWN` before a new send, does not append a rejection, does not cache a never-admitted command ID and does not clear old identities.

Generation-aware startup adopts the validated active-tail capacity rather than charging sealed history against the active writer budget. This permits stopped-state sealing to bound active restart/write growth while preserving immutable historical evidence. Guarded cancel, authoritative flatten, callbacks and terminal evidence keep their existing durable-write rules and are not reclassified as new risk entries.

## Historical command lookup

The cumulative runtime command index is sorted by complete `(agent_id, session_id, command_id)` key. Native lookup uses a binary search, validates the complete decoded key and canonical request hash, and distinguishes `Missing`, `Found` and `Error`. A corrupt or substituted index is never interpreted as a missing command.

Loaded historical commands enter a bounded coordinator cache. Exact duplicate requests return durable state without another venue call; conflicting reuse remains `IDEMPOTENCY_KEY_CONFLICT`. Old identity lookup therefore remains durable without permanent in-memory growth.

## Historical send-attempt window cost

The cumulative send-attempt index is immutable for the selected generation and preserves account, execution domain, timestamp, request identity and journal sequence.

The first query for one selected generation/account/domain/cutoff performs a complete validation/scan of that cumulative index and caches only attempts newer than the requested cutoff. Subsequent queries for the same account/domain with a monotonically increasing cutoff prune that bounded suffix in memory. Ordinary forward-moving PAPER rate checks therefore do not pay O(permanent history) on every preview/place call.

A changed account/domain or a cutoff that moves backwards deliberately invalidates the optimization and performs a complete exact scan. This preserves the existing strict `timestamp > cutoff` and backwards-clock semantics instead of silently resetting a rolling send budget. Active-tail attempts are merged separately and excluded from the sealed-history result by stable request identity so one send is never double counted across a generation cut.

Index validation failure fails closed at the existing rate guard; it never resets the rate budget to zero.

## Storage maintenance and downgrade

`scripts/hepta_oms_lifecycle.py seal --stopped-state` is an explicit stopped-writer operation. V2 publication order is: create and fsync immutable delta generation files, prepare and fsync the lineage-bound active tail, atomically replace the active journal and sync its parent directory, publish `CURRENT`, then publish digest-bound `CURRENT.runtime`. Crash points before/after each boundary are tested.

`scripts/hepta_oms_lifecycle.py export` reconstructs a complete strict JSONL ledger from the selected base generation, V2 deltas and current tail. Export is create-only, fsync'd and validated before success. It has `authorization_effect=NONE` and never replaces the active journal automatically. An older runtime must receive this explicit export rather than silently reading a V2 tail as if it were a complete ledger.

Optional gzip archive maintenance remains a distinct lossless stopped-state operation. Compressed storage is not checkpointing and cannot substitute for generation lineage or decoded recovery validation.

## Terminal mutation universe boundary

Generation-backed recovery and historical command lookup are long-horizon mechanisms. The IB PAPER terminal mutation manifest is a separate qualification/finalization artifact: it enumerates the mutation/correlation universe required by the bounded PAPER campaign and intentionally has its own finite manifest limits. Those limits must not be described as the storage capacity of the OMS ledger or as proof of unlimited unattended production history.

Before widening PAPER qualification into long-running production-like operation, terminalization must either consume a campaign/session-scoped mutation universe or adopt a streaming/digest-bound representation that does not require materializing arbitrary permanent history. Until that separate contract changes, the finite terminal manifest remains an explicit qualification boundary rather than an OMS data-loss mechanism.

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

Native and Python tests cover inclusive/over-limit replay boundaries, bad settings, torn/oversized records, callback atomicity, new-entry pause, generation publication crash points, V1→V2 compatibility, parent/current/sentinel corruption, ancient same-ID duplicate/conflict, no second venue send, send-attempt continuity across a cut, repeated generations and explicit downgrade export.

The opt-in recovery-growth fixture remains useful for measuring legacy full-ledger cost. Generation fixtures prove bounded hot restart and permanent disk-backed identity behavior. Neither source fixture is a target-host multiday soak, physical durability benchmark, Broker qualification campaign or proof that a chosen maintenance cadence satisfies an operational SLO.
