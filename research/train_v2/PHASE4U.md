# Train V2 Phase 4U — presealed first-original source across quote segment rotation

2026-10-12. Independent research-only worktree based on Phase4T d5a0724
(PR #212). Do not merge into a production quote source, model inference,
trader, canonical LedgerWriter, Owner or previously sealed trial.

## Actual hard blocker

The original Phase4T worker chooses the newest numerically named PAPER
quote segment while the fixed new-source window is active. A segment rollover
between pre-registration and first observed quote could cause it to skip
an earlier input in the predecessor segment, then falsely call the next
segment's quote the FIRST source. The lexically-last legacy recovery-live
segment is also not a true current numeric source.

For prospective source firstness there are two valid choices:
(a) prove the complete original multi-segment lineage, or (b) FAIL CLOSED
on any window that overlaps a rotation. Phase 4U implements (b), while
retaining a completely separate signer/replay protocol version.

## Phase4U changes

- The old Phase4T original Worker, signature domain/bytes and verifier
  remain unchanged. Its original SHA256 and previously signed decision are
  not silently upgraded to V2.
- first_source_rotation.py enumerates the bounded original numeric writer
  segments, rejects symlinks, wrong owner/mode, duplicate numeric timestamps,
  malformed numeric-lookalike names, future-created segments and unsafe
  private source roots. The nonnumeric recovery-live source is never accepted.
- The V2 worker seals a bounded original writer-segment identity AFTER the
  original future plan is locally registered but BEFORE the first window
  decision. It creates a separate private, create-only and fsynced
  first-source-cohort.json bound to the exact plan, original registration
  timestamp and source path/creation identifier.
- Any creation of a different numeric segment after registration, including
  during the prewarm lead or after a source has been selected, yields a
  hard HOLD, not the next latest segment. Previously closed segments with
  filesystem modification time after registration also fail closed.
  Local file timestamps are NOT hardware-backed independent attestation.
- The V2 source packet uses a distinct SCHEMA and behavioral policy and
  signs the frozen cohort ID, numeric writer start and original registry
  anchor. The new public-key verifier recomputes these from the current
  original directory at the original cutoff, rechecks the signed source
  prefix, and refuses any cohort mismatch.
- The V2 worker checks the same frozen identity at source selection,
  after external test signature verification and after signed-file fsync.
  Rotation during the signing / fsync interval is a terminal HOLD.
- The new V2 orchestrator requires strict SSH host-key verification.
  It remains a TEST-ONLY Mac RAM-key and source-only research workflow,
  no source Writer credentials or trade authority.

## Historical Phase4T original unchanged

A read-only reinspection of Phase4T's actually signed Mac public witness
and original source plan (2026-10-12 15:15:28 +08) continues to pass with
exactly the same old V1 proof digest. The V2 local numeric cohort selector,
applied only retrospectively to its real UTC deadline and original signed
source path, points to exactly the same original numeric source. This
does NOT reissue the old signed packet or convert it into a prospective
V2 event.

Original quote reference:
~/.local/state/heptatrader/operator-evidence/train-v2-prewarm-20261012/fresh-source-trial-v2/

Phase4U private read-only receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-rotation-fence-20261012/scoped-old-source-readonly-after-final-code.json

Receipt SHA256:
6eaa3bc4e2c35cefd3be5dfe41cc4c3c8f245fbdb156b86418ba8a882ed9bbb6

## Scope and mandatory remaining proof

This is a SINGLE local numeric writer segment with NO ROTATION from the
sealed registration anchor to the first signed source decision.
It is not proof of globally earliest original quote across arbitrary
untrusted/unordered sources, multiple writers, cross-host clocks, or
adversarial filesystem/host time manipulation. The same-UID operator
can change local metadata or source files outside these hash-bound
checks; the original immutable source prefix is always separately
verified.

None of this supplies a canonical root-trusted Generator/Observer,
actual LLM BUY/HOLD/SELL/ABSTAIN and veto population, true logged
behavior propensity, original model REQUEST/PREPARED, native Writer
Decision/Witness before source expiry, DatasetSnapshotV3, official
Artifact Owner CURRENT, or net after-cost independent trading edge.

The following additionally completed NEW test-domain read-only source
experiment is real but still not a product Controller enrollment.

## Final audit: newly created numeric writer directory before first bytes

The original inventory enumerated only existing quotes.jsonl files. A numeric
successor writer directory can exist while its quote file has not yet been
created. That is a real rollover transition and it must NOT be omitted merely
because no source bytes are available. Phase 4U now enumerates all numeric
segment directories and refuses any numeric successor without its original
regular quotes.jsonl. The recovery-live nonnumeric legacy directory remains
excluded. New adversarial test demonstrates refusal of a numeric successor
directory created during the registered window before first source bytes.

This hardens first-source evidence; it is not an assertion that an actual new
trade/LLM strategy event has passed native Canonical enrollment.

## Real new future source event, 2026-10-12, physical Pocket4 + Mac

A separate V2 registry was locally presealed at 16:19:44.800 +08,
44,996ms BEFORE its first eligible future decision window.
Its exact pinned source segment was saved and fsynced in a create-only
first-source-cohort.json BEFORE opening the future window.

A new original PAPER quote was then actually observed within the window:

| Event | Pocket4 UTC+8 |
| --- | --- |
| First valid quote actually received | 16:20:31.353 |
| Mac TEST-ONLY RAM key Ed25519 signature verified | 16:20:31.424 |
| Signed original message fsynced locally | 16:20:31.428 |
| Predeclared source decision cutoff | 16:20:31.903 |
| Original IB reported quote expiry | 16:20:35.811 |

The real original quote-to-fsync time was 75ms; fsync preceded
the decision cutoff by 475ms. The separate source-segment cohort did NOT
rotate between pre-registration, source, Mac signature and fsync.
One original source event entered the local research SQLite ledger.
No market-order command, native product LedgerWriter, economic outcome,
model inference, broker REQUEST/PREPARED or product trust was generated.

Qian-qi orchestrated preopened strictly checked SSH sessions to
MacBook-Pro.local and alex-G1628-04. The private Ed25519 key existed
only in the Mac TEST-ONLY signer process memory and was not copied to
Pocket4. The real source packet is signed in a test domain distinct
from the native canonical LearningLedger domain.

The new V2 public verifier separately reopened the frozen plan,
preregistered source-segment receipt, original PAPER hash chain prefix,
exact Ed25519 public signature, source profile, causal features, original
quote TTL, source-event identity and exactly one locally indexed event.
It was run twice, with identical report SHA256 both times:

fb29b937143073f357aeadfa8a55b79ae58da25fedf2788867a6fa46d45e3c2a

Private original experiment root (never modified or rewritten by the verifier):
~/.local/state/heptatrader/operator-evidence/train-v2-rotation-fence-20261012/fresh-first-source-v2-01/

Private independent receipts:
~/.local/state/heptatrader/operator-evidence/train-v2-rotation-fence-20261012/new-v2-independent-original-source-readback.json
~/.local/state/heptatrader/operator-evidence/train-v2-rotation-fence-20261012/new-v2-independent-original-source-readback-repeat.json

Qian-qi's public transport receipt:
~/.local/state/train-v2-rotation-prewarmed-20261012/real-v2-first-source-test-only.json

This is genuine market-data provenance and TEST-only Mac signing; it is NOT
a real trading prediction or native signed economic evaluation. A recorded
quote expiry is not an executable bid/ask guarantee. X230 independent
reinspection was not claimed: the x230 SSH hostname did not resolve on
the qian-qi host during this run.

## Exact code-tree regressions

- Phase4U first-source rotation/signature tests: 17 passed. Cases include
  live rotation after the preregistration anchor, within the prewarm lead,
  while a signer/fsync is in progress, malformed/future/duplicate writer
  segments, changed predecessors and signed cohort tampering.
- Full core Python + historical-training pytest: 1,896 passed,
  23 pre-existing skipped, 793 passing subtests, zero failures.
- Existing Source/build/ownership lane: 106 passed, zero failures.
- The unchanged V1 original Phase4T public key witness re-verified under
  its original code with identical old report SHA256
  4e57685466f018638aac800fcfe50d01b614728e397db498d3eab42c1c5fbce8.

The V2 source behavior remains opt-in under a distinct schema and
policy ID, and has no installed service. No production switching or
automatic model promotion was performed.
