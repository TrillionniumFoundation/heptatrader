# Phase 4I — Mac/X230 signed, original native LedgerWriter laboratory

2026-10-11, pocket4. Stacked on Phase 4H. Test-only, no deployment,
broker call, trader strategy change, native product authority, or promotion.

## Actual host separation and precise limits

- qian-qi generated and held the ROOT, Evaluator and UnlearningAuthority
  TEST-ONLY Ed25519 private keys.
- MacBook-Pro.local separately generated and held the Generator TEST key.
- qian-ThinkPad-X230 separately generated and held the Observer TEST key.
- Pocket4 ran the ORIGINAL pinned Canonical Rust LedgerWriter and received
  only host-specific public keys and detached Ed25519 signatures.
- The separate qian-qi orchestrator relayed the typed native public
  signing messages using authorized SSH paths; no signing private key was
  copied to qian-qi from Mac/X230 or to Pocket4.

The test hosts and root have distinct keys/controller IDs. This is a
genuine cross-machine cryptographic test, NOT separate organizational
administrators, hardware remote attestation, hardware-secure-key custody,
user-authorized production enrollment or financial observation.
qian-qi owns the available SSH controls across these machines.

## Actual original native writer and V3 path

The Rust lab example was compiled against the exact unchanged
hepta-private-ci commit 78fdb0cf8537e3a84fc6e0a849707559c80881e8
and ORIGINAL Cargo.lock with --offline --locked. The example source is
retained here; it was copied temporarily into the original examples
directory for compilation, then removed. Original source remains clean.

First attempt failed correctly: the synthetic decision candidates omitted
the native protocol's RESERVED explicit ID abstain, using lab.no_trade.
The actual original ledger rejected the trial with MissingAbstainCandidate
before any authenticated decision or witness ACK. Its private failure
log and original 72-byte empty store headers are retained separately.
The nonproduction test fixture was corrected; Canonical was NOT patched.

Final cross-host lab:
1. Qian Root signed the exact native trust-distribution payload. Original
   activation refused a bit-flipped Root signature, then activated a valid
   test-only distribution.
2. A tampered Mac signed Generator decision was refused without advancing
   the original witness. A valid Mac signature authenticated a synthetic
   three-action decision with explicit abstain and Q32 2147483648 (1/2).
3. A Mac Generator signature could not impersonate the Observer role.
   The X230 Observer separately signed the synthetic terminal Outcome,
   advancing the exact native durable witness to 2 records.
4. The qian-qi test Evaluator signed the original native V3 dataset
   freeze. Native revalidate-before-use passed before source withdrawal.
5. A separate qian-qi TEST UnlearningAuthority signed withdrawal of the
   synthetic Outcome. The original witness advanced to 3 records;
   revalidation of that same prior V3 dataset was then correctly rejected.
6. The original DurableLedger and LedgerWitnessStore were persisted as
   distinct files under an isolated private directory (mode 0600).
   No real IB quote, broker request/order, PnL, commission or prediction
   was generated or reclassified as product evidence.

Six role signature requests were actually signed on these hosts:
Root=qian-qi; Decision Generator=Mac; wrong-role Generator=Mac;
terminal Observer=X230; Dataset Evaluator=qian-qi; withdrawal=qian-qi.
The final exact messages, public host metadata and signatures were
retained for AFTER-THE-FACT independent offline Ed25519 verification.

Final public evidence private path on Pocket4:
~/.local/state/heptatrader/operator-evidence/train-v2-crosshost-native-20261011/public_attestations_v3.json

SHA256:
92192af0ae726503116d2d9f977a67f0a5956ee127660006d7252860b1aa4ad2

The independent offline verifier verified all 6 exact canonical native
signature byte strings against the pinned public keys, detecting
tampering, substituted public keys, incorrect roles, and incorrect
native signing domain. Receipt SHA256:
b459ed4ae0458349b6c5c69e1e935d62218662a5234da180e4037abd7e15837e

## No-private-key cold process recovery and controlled tamper

A SEPARATE original native Rust executable recovered the 3 test records
under LedgerRecovery::Acknowledged(witness anchor), verified the actual
independently retained witness frontier and chain digest, and reactivated
the already-signed Root distribution using ONLY saved public keys and
signatures. It refused a tampered COPY of the witness and did not change
the original durable ledger or witness hashes.

The cold recovery was repeated successfully AFTER the ephemeral private
keys were deleted, using ZERO signing requests. Recovery log SHA256:
a22baac59572d45848e210790734d5a50418f0504446d5efafae27f1cea9b8ed

Five ephemeral test-only private key files were individually removed on
their three source hosts, with directory fsync and follow-up zero-file
checks. This does NOT certify physical SSD secure erasure. No actual
production key was touched. Test-private-key removal receipt SHA256:
c1afd3acd531f39af31daf7147ccb8ba801460eec669368db560e58f1b6375b8

## Full regression and remaining HOLD

The original full Python core and historical research regression passed
1,646 tests, 21 skipped, and 728 subtests with zero failures. Final log:
e2b3f5cce36fcc6dea779eaf04ecf356947a4f5fccc8bb57cce770e1652750da

The previous original Canonical full workspace 210 Ledger/Artifact
library tests and 3 explicitly qualified Agentd Owner-host lab tests
remain unchanged. No resident PAPER/FX/BTC/Neuron/Laya service was
modified by this experiment.

Remaining mandatory product acceptance:
- Full ORIGINAL opaque LLM trading behavior candidate enumeration,
  pre-action selected action probabilities (including HOLD and vetoes),
  actual original REQUEST/PREPARED policy identity: NOT present.
- A legitimately host-pinned, root-administrated production trust
  distribution and independently controlled PROD Generator/Observer:
  NOT enrolled by test keys, not provided by SSH host separation alone.
- Real source-time new EUR/USD quote (weekend closed), genuine
  witnessed product Decision/Outcome, actual fills and after-cost net
  reward: NOT obtained.
- Native DatasetSnapshotV3 of genuinely eligible actual sources,
  current-use revalidation under product trust, Artifact Owner/CURRENT,
  physical power failure survival and independent prospective efficacy:
  NOT qualified by this synthetic exercise.

Keep HOLD for trading/model promotion and do NOT put these test keys
into a production service. There is no new daemon, automatic market
watcher, financial model update, or broker trade command here.
