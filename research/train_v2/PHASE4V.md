# Train V2 Phase 4V — fresh original PAPER source + native NO_TRADE witness

Status: isolated LAB-ONLY research test, 2026-10-12 (+08), pocket4 + Mac + qian-qi.
Based on the Phase4U committed source-freeze branch d731960. Nothing was
deployed to original broker, OMS, existing resident Laya, Trial3, original
behavior policy, product root, or Artifact Owner. Zero new broker requests
and zero trading orders.

## Actual new original-source/native event

One genuinely NEW original PAPER quote was independently observed after
a newly registered, create-once first-source cohort. The original segment
was not resampled or retroactively selected. A separately prewarmed
UNMODIFIED Canonical Rust LedgerWriter (pinned original native sources)
used an in-memory TEST root, Mac RAM-only TEST Ed25519 generator and an
explicit model-free deterministic single-candidate policy:
candidate set = [NO_TRADE], selected = NO_TRADE, Q32 propensity = 1.

The recorded Pocket4 clock sequence (milliseconds since epoch):

- Original IB market quote event: 1791801595433
- Original quote locally available: 1791801595875
- Original Mac source signature fsynced: 1791801595903
- Original predeclared source decision cutoff: 1791801596425
- Native Mac signature request: 1791801596358
- Native signature verified/received: 1791801596367
- Canonical native LedgerWriter + independent Witness fsync ACK: 1791801596374
- Precommitted native deadline: 1791801599375
- IB original reported source expiry: 1791801600433

Actual native Witness ACK was **3,001ms before its native deadline** and
4,059ms before original quote expiry; also 51ms before the predeclared
source cutoff. This is TEST-ONLY and not an actual opaque strategy
BUY/SELL/HOLD/ABSTAIN proposal or a broker PREPARED event.

The real native LedgerWriter persisted exactly ONE
AuthenticatedDecisionV2: selected_candidate_id=abstain,
candidate_ids=[abstain], selected_propensity=ProbabilityQ32::ONE.
The native Witness sequence is 1 and its signed chain digest is
724a3c2d1e5ae4ec01121c2449c2e8a9384e8dc8fc9f59e157a424b0a3bb7a5b.
The native ledger is 707 bytes, witness is 160 bytes; both remain private.

## Independent readback rather than self-declared success

New native_fresh_readback.py NEVER writes to original native records.
It first independently recomputes the original hash-chained PAPER source,
validates Mac source public signature, source receipt identity and
registration/rotation cohort. It cross-joins exact source event, original
IB TTL, quote receipt, witness chain and original signed source digest.
It refuses false product/financial claims, altered times and model actions.

Then it creates private 0700 directories and 0600 copied ledger/witness/
public ACK using O_EXCL + O_NOFOLLOW + fsync. It executes the unchanged
Canonical Rust recovery fixture on those separate copies, with the
independently acknowledged LedgerWitnessStore anchor:

1. A real, typed signed V2 abstain Decision reopens with the exact
   independently acknowledged chain digest.
2. A corrupted copied Witness is refused.
3. A ledger reduced to header-only while retaining the acknowledged
   Witness is refused, not reconstructed as empty or success.

Original ledger, witness, public ACK and preserved source are compared
byte-for-byte before/after and remain unchanged. No fresh signing,
trades, model inference or dataset promotion occur during readback.

A total of 16 additional adversarial Python tests cover source/witness
identity, mismatched policy, late ACK, fake product authority, bogus
propensities, symlink/file-mode/size violations, immutable copy reuse
and one-bit corruption.

## Immutable evidence

The original one-shot qian-qi orchestration report:
  /home/qian-qi/work/heptatrader-train-v2-native-sourcebound-20261012/
    first-native-abstain-real-source-a.json

SHA256:
  2d3935433dff2d21f60831c470ab4dbc0a728b1f4d6f2aa6e408274a7a5e2d79

Pocket4 original stored source and actual native records, separate roots:
  ~/.local/state/heptatrader/operator-evidence/
    train-v2-native-abstain-live-20261012/source-first-a/
    train-v2-native-abstain-live-20261012/native-first-a/

Independent private *copy-only* rust verification:
  ~/.local/state/heptatrader/operator-evidence/
    train-v2-native-abstain-live-20261012/readback-native-a/
      independent-recovery-a.json

SHA256:
  1a6ae2c179762134e8cdee1ac5c5409feeeb4cb0698e444c15ccf67ab2818c67

The independently reopened native test chain digest is identical to
the original Writer ACK and unchanged private copied witness frontier.

## Mandatory HOLD

This is a genuine source-time/independent-LAB-signer/native typed
persistence experiment, not a production Generator role or trading model:
- Mac Ed25519 and original Root were TEST keys controlled by the operator.
- The sole Q32=1 is true only for an explicitly FIXED NO_TRADE laboratory
  policy; it is not evidence of the original opaque LLM behavior propensity.
- No existing original model REQUEST/PREPARED, broker trade decision,
  finance-complete executed return or real producer policy was passed.
- No native terminal outcome/CreditBatch, signed DatasetSnapshotV3,
  root-protected revocation head or Artifact Owner CURRENT was created.
- Native physical power-loss and cross-administration owner independence
  remain unqualified.
- Original forward/frozen windows remain unchanged; never backfill them.

The next product gate is genuinely rooted independent host enrollment
with verified original model action set/behavior propensity and an
original pre-order signed Decision+Witness ACK. Only then proceed to
independently observed outcomes, complete after-cost evaluation and
dataset/owner lifecycle. This branch changes no live service config.


## Final code-tree and original-native verification, 2026-10-12

- New native source packet adversarial tests: 12 pass.
- New independent readback, immutable-copy and strict-timeline tests: 16 pass.
- Full Python core/historical pytest: **1,925 pass, 23 pre-existing
  skipped, 816 passing subtests**, zero failures.
- Source/build/ownership test lane: **106 pass**, zero failures.
- Unmodified original Canonical Rust hepta-learning-ledger: **118 pass,
  one pre-existing ignored**, zero failures.
- Three separate, previously ignored, actual copied-original signed V2
  native recovery tests: **3 pass, zero failed or ignored**, proving typed
  original Decision/chain/witness recovery, corrupt witness rejection and
  rejection of lost acknowledged ledger history.
- Cargo clippy --offline --locked --all-targets -D warnings PASS.
- Cargo fmt --all --check PASS.

Pocket4 retained exactly the original 707-byte native ledger and
160-byte independent witness after readback:
- Original native Ledger SHA256:
  31000d33e53b8ec3d0f43dbca134729b2e47dcd903c881f1b5b2078386a66b5f
- Original independent Witness SHA256:
  04dd74b20d6da19a96e770264570eb02b386ef7ccab08dcf895b1342d8329a09
- Original public ACK SHA256:
  3eea6c374c8ef038fec51739ae45f3898b0acec761836d9af9159c26405615b5

The independently recovered chain digest was exactly
724a3c2d1e5ae4ec01121c2449c2e8a9384e8dc8fc9f59e157a424b0a3bb7a5b.

Exact tested source identities:
- Pinned native Rust example:
  0da6c6163e8d6042f91ebe53ac39874d51c3cf2cc8dcff52e9e8b8609e8a0235
- Preexisting native compiled binary:
  41b6be2ac9f85e0f4f592016037c82819a87df04e843fef8d0d316a08984924e
- New independent Python readback:
  b970008c475dc9c0a885160c7d5a3bc5bb2fbb2bb38538dd911bb1609d30034b

## Separate continuous PAPER trading observation

The experiment did NOT send an order, trade or broker API request. The
running preexisting PAPER FX/BTC drivers were not disabled. During the
test window, the independent strategy PnL snapshot changed from a flat
EUR/USD position and realized -1339.1561976525 USD to approximately
-89432 EUR and a changing negative strategy PnL. This does not prove
this research test caused or prevented the autonomous PAPER trading,
and it cannot be recorded as an experimental fill/reward. The current
account_complete status remained false; original Execution had
NRestarts=293, unchanged during final verification. This is a test-only
data/control-plane validation, not an after-cost efficacy result.

No product Root, independent institutions, original opaque LLM policy
action propensity, order PREPARED, financial reward or model release
authority has been approved.


## Strict original source cutoff follow-through (2026-10-12)

The independent readback verifier now requires actual native LedgerWriter
witness ACK no later than the original source decision cutoff, in addition
to the separate later precommitted native deadline and original IB expiry.
Previously, a late ACK between these two deadlines would pass even though
the quote's original decision-window promise had already closed.

The unmodified original Phase 4V public ACK/source/intent and independent
source-verifier report were re-joined in memory: true native ACK
1791801596374, original source cutoff 1791801596425, exact positive margin
51ms, native later deadline margin 3001ms, original quote-expiry margin
4059ms. The original signed native transaction remains VALID TEST ONLY.
An adversarial **private in-memory copy** with ACK at source cutoff+1ms,
which was still before the later native deadline, is now explicitly denied
as TEST_NATIVE_ORIGINAL_TIMELINE_NOT_CAUSAL.

No original signed bytes, native Ledger/Witness/ACK, broker request, trading
order, learned model or production service has been changed.


Final regression on the isolated cutoff-fix commit: 18 independent
readback unit tests passed; the exact entire Python and historical
pytest suite returned 1,927 passing, 23 preexisting skipped,
816 passing subtests, zero failures. Source/build ownership lane
returned 106 passing, zero failures. No native Rust source was modified;
the earlier 118 original native tests and three original-copy tests
remain earlier evidence, not re-run in this follow-through.

The real original receipt recheck establishes exactly +51ms of true
source cutoff slack (ACK 1791801596374, cutoff 1791801596425).
A privately constructed ACK one millisecond AFTER source cutoff but
still earlier than the later native deadline was rejected.
These are local-clock test-only native constraints, not an actual
opaque model PREPARED or canonical product owner grant.
