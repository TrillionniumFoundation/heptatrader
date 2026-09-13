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
