# Train V2 Phase 4T — independent verification of an actual fresh PAPER source

2026-10-12, Pocket4 +08. Isolated research-only worktree based on the
unchanged Phase4S commit faf58b2. No production trader, IB gateway, canonical
native ledger, Agentd Owner, old Trial3, Laya admission rule, or model was
modified or restarted.

## Real freshly observed source proof (NOT a product model prediction)

A previous, newly preregistered source-only experiment on 2026-10-12 actually
observed a new original IB PAPER quote. A preopened Mac TEST-ONLY RAM key
signed its exact source packet and Pocket4 fsynced its research witness before
the already-predeclared decision cutoff, without an IB order or new broker
request. The original signed worker source is preserved byte-for-byte.

Pocket4 locally observed:
- Original source available: 2026-10-12 15:15:28.138 +08.
- TEST-only Mac signature verified: 15:15:28.192.
- Locally durable signed evidence: 15:15:28.193.
- Predeclared source decision cutoff: 15:15:28.688.
- Original IB-reported quote expiry: 15:15:32.675.
- Source status: TEST_ONLY_ACTUAL_SOURCE_SIGNED_AND_LOCALLY_INDEXED.
- Exactly one source event, no economic Outcome or model inference.
- Original source quote was at 15:15:27.675.

The new independent verifier reopens the registered source, validates the
unchanged original source chain prefix, exact first selected quote within
the chosen numeric segment, exact registered plan, public Ed25519 test
signature, signed packet, original period and TTL, only one SQLite research
event and no outcome/review batch. Repeated verification reproduced the
identical original report body/digest without issuing a new signature,
prediction or order.

The first-source qualification is LIMITED to the chosen recorded segment.
Across source segment rollover, there may be older valid quotes in an
adjacent segment; no globally earliest original quote guarantee is claimed.
This matters for any future official forward-trial first-input requirement.

## Source/reproducibility hardening

The source-end worker originally used for the recorded event is copied into
this research branch with byte-for-byte identical contents; its source SHA
remains bound into the old original event. The orchestrator no longer
imports a temporary qian-qi-only Python module. Instead, the signer
transport is imported from the checked-in prewarmed_signer_latency module.
Future Mac and Pocket4 SSH sessions explicitly enforce
StrictHostKeyChecking=yes. Both aliases were actually tested with the
strict option using the previously registered host keys, without changing
host-key storage, distributing SSH credentials or enabling unknown hosts.

SQLite readback now requires the source-only WAL to be completely checkpointed
before inspection. The verifier opens immutable read-only SQLite to prevent
creation or modification of -shm sidecars; an unfinished WAL, unsafe file
mode or symlink causes HOLD. Unit tests verify the lack of sidecar creation,
immutable database mtime and refusal of an uncheckpointed journal.

The independent verification runner does create and clean up a private
temporary copy of the already hash-bound source prefix. That is NOT a live
original source write, and its result cannot be promoted to canonical fact.

## Research versus product authority

A Mac Ed25519 TEST key generated under the operator's SSH account is not an
independently enrolled Canonical producer principal or a broker quote
signature. The original plan is locally preregistered, not signed by
product Root. Neither original behavior-policy candidate population nor
actual selected action propensity, original LLM model prediction,
PREPARED trade deadline, complete broker fills, after-cost economic reward,
native LedgerWriter/witness, native DatasetSnapshotV3 or Artifact Owner
CURRENT is proved by this source-only experiment.

The snapshot must not be used to backfill failed earlier windows or to
upgrade the old source-only data as a signed production trade decision.

## Immutable evidence

Original sealed witness and research ledger, untouched:
~/.local/state/heptatrader/operator-evidence/train-v2-prewarm-20261012/fresh-source-trial-v2/

Independent source/public-signature/ledger readback:
~/.local/state/heptatrader/operator-evidence/train-v2-prewarm-20261012/immutable-reverification-v4.json

Repeatable independent verifier result SHA256:
4e57685466f018638aac800fcfe50d01b614728e397db498d3eab42c1c5fbce8

The verifier's output includes no private signing key or account credentials.
The original source experiment was neither an executable model prediction
nor a native canonical product event.

## Next acceptance gate

1. Add first-source selection that spans segment rotation without resampling
   (or fail closed when the original cohort overlaps a rotation boundary).
2. On a genuinely NEW future session, bind the original logged action
   population, actual behavior propensity and original REQUEST/PREPARED
   identity; refuse confidence-as-propensity.
3. Use product-root enrolled independent generator and observer controllers
   and native LedgerWriter/witness with signed original typed bytes already
   physically escrowed before append and before the valid quote/PREPARED cutoff.
4. Separately qualify DatasetSnapshotReceiptV3, CURRENT Artifact Owner,
   new prospectively observed after-cost PnL and a safe PAPER cutover.

This phase is source timing and research signature verification, not any
grant of native model selection or trade permission.
