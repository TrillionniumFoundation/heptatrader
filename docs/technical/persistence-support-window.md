# Persistent-state support window and retirement decisions

Status: CURRENT
Applies to: supported source readers and deployment-specific artifact pairs

## Three separate compatibility axes

| Axis | Current writer / protocol | Compatibility claim |
|---|---|---|
| Session wire | HSS1 | exact operation field sets; unknown/malformed input rejected |
| Execution / event wire | HEX1 v10 / HEV2 v2 | exact versions; no inferred rolling-wire compatibility |
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

## Long-term lifecycle design: PROPOSAL, not an installed capability

The current supported runtime still replays a bounded complete journal and
retains every durable command identity in memory. The 80-percent entry pause
and stopped-state gzip maintenance do not change that contract. A deployment
must measure its actual event/byte growth and recovery RSS/time, arrange
maintenance before the bounded envelope is exhausted, and preserve guarded
exit evidence. No default budget is a proven multiday SLA and no automatic
budget increase, identity expiry or ledger deletion is introduced here.

The next lifecycle implementation must replace both full event materialization
and the all-history in-memory command projection. Optimizing only one leaves
the other unbounded. The intended components and ownership are:

| Component | Durable contents | Required behavior |
|---|---|---|
| Immutable sealed journal segments | Original event bytes, range and digest | Preserve audit history; stream validation without a full event vector |
| Versioned checkpoint | Projection at one verified durable cut, open/uncertain commands, owners, fences and terminal witnesses | No partly applied generation becomes visible |
| Disk-backed historical command index | Full owner/session/command key, normalized request hash, durable outcome and source correlation | Exact old-ID duplicate/conflict lookup without retaining every historical record in RAM |
| Active journal and hot projection | Events after the committed cut, mutable/unresolved identities | Bounded incremental replay; unresolved possible sends cannot be evicted |
| Generation manifest | Ordered segment/index/checkpoint identities, byte/range bounds and parent generation | One atomic pointer selects a complete, mutually consistent generation |

The historical index cannot use a hash alone as identity. Lookups must compare
the complete normalized key and payload hash; hash collision, corrupt row or
missing source range is failure, never "not found" followed by a new send.
An accepted command is not disposable merely because its order is terminal.
Known rejected commands, successful cancels, exact flatten no-ops, owner fences
and terminal acknowledgements all retain their current replay semantics.
Late callbacks must resolve against the retained venue/connection correlation;
unknown, conflicting or incomplete evidence keeps the existing admission fence.

Initial migration should be stopped-state, with the same writer exclusion and
trusted namespace requirements as lossless archive maintenance. Validate the
entire source ledger before deriving the cut; write new checkpoint/index files,
validate producer/reader parity, synchronize each file, write and synchronize a
manifest, then atomically publish the generation pointer and synchronize its
directory. Keep the previous generation and all source segments. A failed step
must leave either the previous committed generation usable or an explicit
fail-closed incident, never permission to guess a newer authority state.
Startup verifies generation identity, supported format, bounds, ordered ranges,
checksums and index/checkpoint agreement before projecting the active tail.
A corrupt current committed generation must not silently fall back to an older
fence or command history. Orphan temporary files confer no authority.

A checkpoint must not be called complete until execution identity, send-attempt
rate history, owner/session fences and HSL terminal acknowledgement bindings
are recoverable together. The one-artifact rollback fixture must prove both the
upgrade and an explicit downgrade/export path. Older readers must reject new
formats rather than interpreting a missing active log as an empty clean ledger.
Retaining a gzip file or copying an old lease alone is not this migration.

Implementation acceptance requires real-source old/new fixtures covering each
crash point, torn/changed manifests, missing or reordered segments, index hash
collisions, late callbacks, all durable boundaries of an uncertain send,
old-ID duplicate/conflict queries, fenced/terminal owners, cancellations and
flatten at capacity, and restart/rollback with identical economic outcomes.
Scale tests must report decoded bytes, record and identity counts, peak RSS,
lookup latency and recovery time as history grows; a hard-coded small fixture
or a renamed "checkpoint" file does not demonstrate bounded resource use.

Until those producers, readers and regressions are implemented and accepted,
`OMS-LIFECYCLE-002` remains OPEN. This proposal neither changes a persistent
schema nor enables an alternative order path, retention policy or trading mode.
