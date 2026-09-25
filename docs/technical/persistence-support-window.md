# Persistent-state support window and retirement decisions

Status: CURRENT
Applies to: supported source readers and deployment-specific artifact pairs

## Three separate compatibility axes

| Axis | Current writer / protocol | Compatibility claim |
|---|---|---|
| Session wire | HSS1 | exact operation field sets; unknown/malformed input rejected |
| Execution / event wire | HEX1 v11 / HEV2 v2 | exact versions; no inferred rolling-wire compatibility |
| OMS journal | schema 4 | maintained parser accepts historical schemas 1–4; unknown versions fail |
| Lease plaintext | HSL8 inside encrypted HSL2 envelope | dedicated historical parsers/migrations, not arbitrary rollback |

The encrypted store is bounded to 2 MiB, with key input bounded to 65 bytes.
Plaintext HSL1–HSL6 use explicit historical layouts. Pre-owner PAPER HSL4/HSL5
have narrow cleanup-only migration: fixed identity, trusted source/key/lock,
expiry, and absence of predecessor/fence/recovery state are required. HSL7
lease/tombstone rows remain recognized but obsolete acknowledgement rows are
rejected. Current HSL8 acknowledgement rows bind both preliminary audit and
Execution terminal receipt. Do not replace these parsers with an "accept any
old version" branch or generate old fixtures with the current serializer.

## Exact HSL8 row order

The first plaintext line is `HSL8`. Subsequent rows are tab-separated and
newline-terminated; variable text is hex encoded. This describes private
plaintext inside the authenticated encrypted envelope, not a format to export
into logs or backup manifests.

`R` row fields after the tag, in order:

1. templateId(hex), issuer(hex), token(hex), agentId(hex), sessionId(hex).
2. peerUid(decimal), expiresAtMs(decimal), leaseGeneration(decimal).
3. predecessorToken(hex), predecessorGeneration(decimal), fencePending(0/1), fenceComplete(0/1), fenceReason(hex).
4. recoveryOnly(0/1), recoveryCommandId(hex), paperFinalizationRequired(0/1), ownerAccount(hex), ownerExecutionDomain(hex).
5. paperFinalizationState(decimal enum: None=0, FencePending=1, FenceComplete=2, AuditSealed=3).
6. recoveryId(hex), finalizationId(hex), expectedOwnerSetSha256(hex), expectedOwnerCount(decimal), ownerTokenSha256(hex), finalizationReceiptSha256(hex), finalizationReceipt(hex).

`A` row fields after the tag, in order:

1. recoveryId(hex), finalizationId(hex), expectedOwnerSetSha256(hex), expectedOwnerCount(decimal).
2. receiptSha256(hex), receipt(hex), terminalReceiptSha256(hex), terminalReceipt(hex).
3. acknowledgingOwnerTokenSha256(hex), acknowledgingOwnerGeneration(decimal), acknowledgingOwnerIssuer(hex).
4. terminalizingOwnerAgentId(hex), terminalizingOwnerSessionId(hex), terminalizingOwnerAccount(hex), terminalizingOwnerExecutionDomain(hex).

An acknowledgement row or finalization tombstone can never become a live lease.
Hex encoding is not encryption; possession of these bytes must not imply
permission to provision, rotate, reopen a finalized owner or suppress a fence.
The current serializer/old-format migration tests are authoritative for exact
encoding and hostile input cases.

## Supported deployment pair is an explicit decision

The repository's distinct-artifact rollback test uses pinned reference
`d003f54c7c6c2bd19002627f2bcd9081228b01cd`. It is a regression reference, not an
indefinite promise to support every commit after it. The tested previous →
candidate → previous → candidate shared-state sequence proves only that pair
and its fixture states. It does not prove arbitrary historical active-order,
uncertain-send, key migration or schema-downgrade behavior.

Before deploying, record previous/candidate package digests, source IDs, actual
schema/key custody, owner/domain and host configuration, and run the recovery
procedure with an offline consistent checkpoint plus authoritative venue state
where applicable. Restore the encrypted lease, matching key and journal as one
protected stopped-state unit. Never restore an old lease alone over a newer
fence generation. No production backup API or general schema-conversion tool is
introduced by this cleanup.

## Retirement rule

There is no verified inventory of all deployed old formats in the source tree.
Therefore historical HSL parsers and shared legacy headers are retained, rather
than deleted to reduce code count. Retire one format only after the owner
identifies every supported consumer, migrates its retained state, proves failed
and interrupted migration behavior, and declares an explicit minimum supported
format. Keep a reject fixture for the retired format. The same rule applies to
an older artifact pair: replacing the reference needs actual state recovery
and a documented support-window decision, not merely a changed SHA constant.

Current deployment work belongs in [`../gap-register.json`](../gap-register.json),
including `HOST-ROLLBACK-001`. This support contract does not copy issue status
or declare actual-host acceptance from a source check.

## Optional gzip storage compatibility

See [lossless stopped-state maintenance](oms-archive-lifecycle.md) for optional gzip storage,
writer exclusion, decoded recovery budgets, crash handling and explicit
expansion before downgrade. It preserves all event bytes and command identities;
it is not online truncation or a general N-1 compatibility claim.

## Long-term OMS lifecycle: current support boundary

Generation-backed OMS recovery is now an installed repository capability rather
than the proposal that previously occupied this section. The authoritative
engineering details live in [OMS recovery capacity](oms-recovery-capacity.md);
this document records only the persistence support boundary so it does not
become a second implementation specification.

Current V2 stopped-state sealing publishes immutable delta segments, cumulative
full-key command/send indexes, bounded hot replay, an active-tail sentinel and a
digest-bound current generation. Native coordinator startup validates the
selected generation and replays only bounded hot/tail state; historical command
identity remains disk-backed and exact old-ID duplicate/conflict lookup does not
require retaining every command in memory. Simulator V2 generations also carry
a compact economic checkpoint for position, admitted-order count and order-ID
watermark. A normal V2 restart has no full-history simulator replay fallback.

Compatibility is deliberately asymmetric:

| State | Current behavior |
|---|---|
| no generation store / legacy journal | complete bounded journal validation and replay remains supported |
| V1 generation | maintained compatibility reader; stopped-state sealing may migrate it to V2 |
| current V2 with simulator checkpoint | bounded checkpoint + hot/tail restart |
| older V2 without simulator checkpoint | runtime fails closed; stopped-state lifecycle sealing reconstructs and republishes the checkpoint |
| downgrade to a legacy reader | explicit create-only validated JSONL export; never reinterpret the V2 active tail as a full ledger |
| long V2 lineage | explicit stopped-state rebase may collapse verified history and prune only after the new parentless authority is durable and reverified |

No maintenance operation expires command identity, discards logical event
history, resolves an uncertain broker send, or changes PAPER/LIVE authority.
Corrupt current pointers, manifests, indexes, lineage bindings or active-tail
sentinels fail closed instead of falling back to a parent generation.

Repository acceptance covers generation publication crash points, V1/V2
compatibility, ancient duplicate/conflict lookup, sorted send-attempt continuity,
streaming index verification, owner/session terminal summaries, simulator
checkpoint restoration, explicit downgrade export, rebase/prune crash points,
long-lineage behavior and measured source/installed-process cost curves. Those
tests are repository evidence for their exact artifact; they are not a
target-host durability or multiday SLO.

Accordingly, `OMS-LIFECYCLE-002` is CLOSED for the repository scope recorded in
`docs/gap-register.json`. Target-host rollback, retention/maintenance cadence,
physical durability, multiday operating cost and future schema retirement remain
separate deployment decisions under the external host gaps. A future persistent
format change must add old/new migration and rollback evidence before support for
an existing reader is retired.

## X230 bounded growth observations — 2026-09-25

Measured source: `287b5c5e12b57b6cf241bbdfa678fbde85535d58`, tree
`e203c912bab6d761ccf8aa451a57d19867c2758d`. This is the measured candidate,
not an assertion about any later HEAD. Host: ThinkPad X230, Intel i5-3230M,
Linux 6.8.0-139, ext4 `/dev/sda5`, GCC 13.3.0, CMake Release, IB API disabled.
The existing native probes ran as an ordinary user on the actual filesystem.
Inputs and venue callbacks are synthetic; these are not Broker executions.
Fresh-process RSS is measured without dropping the filesystem page cache.

| Retained lease acknowledgement groups | Encrypted bytes after cleanup | Reopen ms | Largest sampled persistence ms |
|---:|---:|---:|---:|
| 1 | 23572 | 1.530 | 1.261 |
| 8 | 188058 | 13.886 | 9.667 |
| 88 | 2071018 | 123.207 | 48.682 |

At the 88-group fixture, ordinary admission was refused while fence/removal
still committed; oldest retired-owner reuse was rejected after reopening.
Its 2 MiB format bound is unchanged. Two/three persistence observations are
not a p99 estimate or a storage SLO.

| Synthetic orders | State | Recovery ms | Peak RSS KiB | Maintenance ms | Retained bytes |
|---:|---|---:|---:|---:|---:|
| 32 | sealed | 2.136 | 7936 | 102.536 | 240896 |
| 32 | rebased | 2.502 | 8064 | 127.957 | 167981 |
| 128 | sealed | 2.921 | 8064 | 125.894 | 927479 |
| 128 | rebased | 4.205 | 8192 | 167.398 | 662136 |
| 512 | sealed | 6.175 | 8064 | 254.322 | 3683122 |
| 512 | rebased | 11.368 | 8064 | 378.469 | 2644133 |
| 8192 | sealed | 76.672 | 8064 | 2086.130 | 59081662 |
| 8192 | rebased | 144.037 | 8064 | 3441.121 | 42432333 |

Every stage checked ancient same-ID duplicates, changed-payload conflicts and
zero resends in a fresh process. The 8,192-order run has four intermediate seals
(2,048/4,096/6,144/8,192), followed by rebase; this is not 8,192 live Broker orders.
Rebase reduces retained duplicate indexes but does not make recovery time constant:
the final parentless history still requires integrity I/O. No claim is made for
larger histories, physical power failure, unattended multiday operation, remote
filesystems, high concurrency or production latency. The measured envelope is
a development-host observation, not a new maximum supported workload.

Reproduction uses the existing test executables, after building the same source:

```sh
build/tests/hepta_session_supervisor_lease_store_migration_tests --writer-exclusion
build/tests/hepta_audit_journal_lifecycle_tests
build/tests/hepta_session_supervisor_lease_store_migration_tests --lease-history-growth
build/tests/hepta_execution_coordinator_tests --generation-growth 8192
```

Raw JSONL SHA-256 (retained with the implementation-session evidence, not runtime authority):

- `lease-cost.jsonl`: `99ca46f365e2bb124bbfa5b8eeb24ceeb2f62a0b138ac1593626a3991d4c65ee`
- `oms-32.jsonl`: `345528dae02bd7508c9ec9b5d1f156a01bd9e6655cffd3f4ef6d350c7c3cec4f`
- `oms-128.jsonl`: `ddc0dcefda4337ecd68e52ccb2bfd98f94d5234c0d932f21e5803436d8e01884`
- `oms-512.jsonl`: `c0942ba9a1b47af9620733d9105d7e439f6495ad94813d0c56bf1a017baab8b4`
- `oms-8192.jsonl`: `40d0acfd06c5b61db0e167830a4eb162049de5df1df33000aa092857355d3226`
