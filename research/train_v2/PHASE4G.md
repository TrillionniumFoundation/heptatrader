# Train V2 Phase 4G: preregistered shadow pre-action policy (NO ORDERS)

Date: 2026-10-11, Pocket4. Stacked on Phase 4F.
**Research only: no trading, training, native product or learning admission.**

## Why this is a NEW shadow policy

The original EUR/USD/BTC PAPER strategy uses an opaque model returning
only action, confidence, and rationale. Confidence is not the probability
of action under the entire original behavior policy, especially after
position/risk/market vetoes. The historical OPEN subset misses HOLD and
rejected decision opportunities. No past decision is backfilled.

This code defines the entirely new policy
SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1, whose signals are
NO_TRADE, UP_SIGNAL and DOWN_SIGNAL, NEVER broker orders.
The explicit selector draws secrets.randbits(2) and maps 0/1 -> NO_TRADE,
2 -> UP_SIGNAL, 3 -> DOWN_SIGNAL. Thus the defined conditional selection
probabilities are exactly 1/2, 1/4, 1/4. They map to the original
ProbabilityQ32 raw integers 2147483648, 1073741824, 1073741824.
When the original verified spread exceeds 3 bps, the full legal
signal set contains only NO_TRADE, with probability 1 and raw 4294967296,
and performs no RNG draw. Each selected receipt binds the generator-code
digest, full candidate set, omitted_count_bound=0, explicit
two-bit draw, exact probability and original quote identities.

This is a genuinely specified selection distribution for this new
shadow policy, NOT for the original trading LLM, and it does not
independently certify the operating system random generator or issuer.

## Source and preregistration checks

- Exact private, owner-matching PAPER resident config and period SHA256,
  actual feed segment path, symlink and source-size rejection.
- Existing original Laya SHA256 quote-chain and IB-reported expiry reader;
  original source is locally hashed, NOT broker cryptographically signed.
- At least six unique, chronological locally witnessed source quotes,
  maximum history gap 30s, latest source age at most 1.5 seconds,
  and no expired or future input.
- The existing Phase 3 local immutable fsynced research plan must be
  revalidated against its SQLite registration and source-root identity.
  It must be sealed at least 30 seconds before the current action
  and cover the actual pre-action time. Missing or tampered plans HOLD.
- Decision time taken after the full source snapshot is read (not backdated
  before a possibly newer source event). Recheck the local window deadline
  and quote freshness at the post-selection commit decision time.
- The standalone CLI fsyncs a deterministic immutable local
  (registered plan, original source quote, policy) episode claim BEFORE
  drawing randomness. A repeated/unknown-ACK original episode HOLDS
  instead of rerolling the shadow action. This is local crash ambiguity,
  not a product LedgerWriter/independent witness implementation.
- Real-time source freshness rechecked after selection. Receipt created
  only once (O_EXCL) and file+containing-directory fsynced. No model call,
  broker query/order, service restart, or product owner write.

This is NOT independent product preregistration under a host-pinned
root signature, nor an original policy Generator/Observer enrollment.

## Actual Pocket4 results

The resident original EUR/USD PAPER feed is active but latest checked
files had zero valid authoritative quotes and many
AUTHORITATIVE_QUOTE_UNREADY failures. The research policy did not
invent new quotes or backdate historic records.

A new isolated Phase 3 local research plan was sealed and revalidated
under the existing SQLite and immutable plan receipt:

- Plan ID:
  5dc8f5b9f940ec4ada5771da986d4329e6a83a4a5f7619af7169f28df8cccaae
- Registration lead to first allowed decision: 74,996ms.
- Registration receipt SHA256:
  3c4810961bbae5ed6f16bde8d5909701264d9423869edf4ea117eeb255824624
- Actual-source CLI with matching registration returned
  HOLD_NO_ELIGIBLE_FRESH_SOURCE / INSUFFICIENT_ORIGINAL_QUOTE_HISTORY.
  No shadow decision was emitted.
- Real-source attempt SHA256:
  fefb6390d0ae7ae2d03bf5ea16aba0d08340527389ac08e0ce99e8974aa1a0a1

Private original-source evidence:
~/.local/state/heptatrader/operator-evidence/train-v2-preaction-shadow-20261011/local-preregistered/

No automated future watcher was installed and no future observation or
cost-complete settlement was made. The local preregistration itself is
not an independent forward trial or performance evidence.

## Separate synthetic integration; NEVER real market evidence

A completely isolated control test created SIX FABRICATED quotes with an
artificial IB-shaped local header. This demonstrates that a source's
SHA256 chain alone is not a broker authenticity proof. The actual
host-clock Phase 3 SQLite preregistration preceded the final
synthetic-only shadow decision by 38,314ms, and the first allowed
decision was 37,997ms after sealing. It exercised the genuine
read-only quote parser, after-source-read decision timestamp,
fixed policy/choice probability, and atomic local receipts.
Two independent PYTHON CLI PROCESSES simultaneously tried the exact
same plan/quote/policy episode: one successfully claimed its identity,
the other received HOLD without a second random selection.

- Final synthetic-only decision: NO_TRADE, two-bit word=0, exact Q32
  selected probability 2147483648 (1/2), NEVER a brokerage order.
- Deterministic episode-claim fsynced BEFORE drawing randomness;
  second attempt for the same plan/quote/policy failed closed as
  PREACTION_EPISODE_ALREADY_CLAIMED_INDETERMINATE, not rerolled.
- Final cross-process synthetic-only integration receipt SHA256:
  9f03f40fa914107c23bf59a135e4c2d85de3c6f7a0056bf21edb614f3ffcd0f2
- Product root identity, real market, V2 product LedgerWriter append,
  Observer/corrections, SnapshotV3, Owner/CURRENT, actual fills
  and commission/slippage/financing/net PnL: **NONE**.

Private explicit synthetic-only fixture:
~/.local/state/heptatrader/operator-evidence/train-v2-preaction-shadow-20261011/integration-lab-fixture-v4/

## Crash/retry and first-open concurrency

The shadow CLI uses one immutable O_EXCL claim for each registered-plan,
original-source-receipt and shadow-policy episode, synchronizing the
claim file and containing directory BEFORE RNG selection. Repeated or
unknown outcomes HOLD. This is only an isolated local claim, not
product-ledger durable ACK or a real hardware power-loss test.

An unrelated genuine Python full-suite failure exposed an earlier bug
in the Phase 1 orchestrator.database first-open SQLite path: two
concurrent processes could race on PRAGMA journal_mode=WAL and one
could fail with SQLITE_BUSY (database is locked) before it reached the
transaction's existing BEGIN IMMEDIATE. The failed exact suite is
preserved, not relabelled passing:

~/.local/state/heptatrader/operator-evidence/train-v2-preaction-shadow-20261011/release-source-core-historical.log

First failure log SHA256:
17fb2e2c6ce41d7f1bef968e9c4061390cd50f531692df3ed8a5a5da8e770c8f

Both Phase 1 event ledger and Phase 3 registry now retry ONLY SQLite
BUSY/LOCKED during idempotent WAL and schema initialization for an
absolute bound of 5 seconds. Other SQL/schema/permissions failures
still reject and initialized connections are closed upon exception.
The actual event identity remains serialized through the existing
BEGIN IMMEDIATE. No deployment/broker/service mutation was made.

The race test repeated 20 complete cycles of 3 critical process
tests per cycle (including exact input deduplication and separate
WAL/registry first opens): **60/60 passed**. Stress log SHA256:
58ba4608ef5b61450bcd338080915dad06802805ed153911bc1ef8767bc1affc

## Tests

- New policy source/expiry/two-bit RNG/immutable local plan/episode-claim:
  26 passed, 14 subtests, zero failures; a second test confirmed real
  independent-process CLI competition on the same synthetic episode.
- SQLite bootstrap error classification, concurrent fresh opens,
  original event exactly-once and registry tests:
  83 passed, 14 subtests (combined focused partition), zero failures.
- First full core+historical test with the original SQLite initialization
  bug failed once as documented above (do not erase this failure).
  After retry correction, the complete test set (including seven new
  SQLite bootstrap tests) passed: **1,617 passed, 21 skipped,
  725 subtests**, zero failures; corrected test log SHA256:
  125e01b05c279f66b143597858bcb4ce51ff7b7e0a5e9a7bd62828aee412db9c.
  The subsequent final rerun with exception-safe SQLite connection cleanup
  again passed **1,617 / 21 skipped / 725 subtests**; log SHA256:
  4cc3f1c11f630b7b756f72537522780303a879d51347dabfbda892531a81a34a.
- The original unmodified Canonical workspace remains pinned to exact
  SHA 78fdb0cf8537e3a84fc6e0a849707559c80881e8 with 193 Cargo
  members, 1,517 resolved packages and 1,316 lock-verified archives.
  Original LedgerWriter and Artifact Owner native library tests pass
  210/210. Original codex-hepta-types native tests passed 53/53;
  Q32 type receipt test log SHA256:
  6eff32117db212a16936a78ef56ab04f8278f1c5bee2d152e92a3e59fe887a19.

## Final Pocket4 research qualification seal

Immutable private summary (source files, live HOLD, synthetic lab,
first SQLite failure, bounded retry, full tests, native Q32 and
multi-process experiment digests) is stored at:

~/.local/state/heptatrader/operator-evidence/train-v2-preaction-shadow-20261011/final-phase4g-qualification-v1.json

SHA256:
b77f4e354dc57445e3f590d1bbe6b70e3df514db60417ad7d278a3841ce59804

All real broker/orders, real forward outcome, product-root signatures,
native Product LedgerWriter/Observer/witness, DatasetSnapshotV3,
and independent after-cost reward admission remain FALSE.

## Mandatory next product gate (HOLD)

The actual order-capable Generator must enumerate legal actions and
true action probability at the original decision time, including HOLD and
all risk vetoes, before any effect. It must authenticate under a real
host-pinned root distribution with independent Observer controller,
credential and signing key, and write genuine product decisions through
native LedgerWriter and witness before any acknowledgement. Outcomes,
corrections/revocation, freeze DatasetSnapshotReceiptV3, final-use
revalidation and independent unseen after-cost effectiveness remain
mandatory. No code here grants original policy behavior propensity,
training, trade, canary, selection, promotion, or release authority.

### Reproduction

Run with PYTHONPATH=research:research/neuron_learning:scripts:
python3 -m pytest -q tests/python/test_train_v2_preaction_shadow_policy.py

The standalone read-only CLI takes --config, --source-file,
--registry-root, --plan, --output; a missing or stale source or
unregistered plan only produces HOLD.
