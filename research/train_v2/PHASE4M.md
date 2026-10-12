# Train V2 Phase 4M: exact signed native retry, witness-lag and expiry

2026-10-12, Pocket4. Stacked on Phase 4L's actual locally witnessed
original IB PAPER EUR/USD event. No new forecast, market data request,
model inference, broker order, source credential, production identity,
artifact, system service or trading-policy change.

**New product blocker identified and reproduced: recovery after a
durably committed but not independently ACKed event can collide with
signer expiration. An older signed event must NOT be newly authorized
by passing an untrusted caller-chosen historical clock to the writer.**

## Real original experiment reused exactly without new keys

All six native replay scenarios derive from the real, previously
frozen Phase 4L market event:

- One actual original EUR/USD source-time new-shadow NO_TRADE
  decision, selected ProbabilityQ32 raw 2147483648 (1/2), at
  2026-10-12 09:17:01.699 China +08. This probability only belongs
  to the new shadow CSPRNG, NEVER the opaque old LLM.
- A Mac TEST Generator signed the exact native Decision at
  09:17:14.660, before the five-minute outcome was knowable.
- The original unmodified Canonical LedgerWriter durably appended
  and independently witnessed Decision sequence 1 at 09:17:14.922.
- At 09:22:04.136 the genuine locally hash-chained PAPER EUR/USD
  original endpoint became observable; X230 independently
  recomputed -0.80241080 bps from 146 original source-copy quotes.
  This is PRICE, not an actual order, fill or economic net PnL.
- X230 TEST Observer signed the original typed PRICE-only Outcome
  after maturity; original LedgerWriter appended witnessed sequence
  2. The original in-memory test-only DatasetSnapshotV3 was frozen
  and revalidated in Phase 4L. These are NOT market-eligible V3 rows.
- The native Root, Mac Generator, X230 Observer, qian Evaluator,
  qian Privacy keys were all TEST-ONLY and their five private key
  FILES were already removed from host filesystems in Phase 4L.

Pinned exact unchanged original Canonical source:
hepta-private-ci@78fdb0cf8537e3a84fc6e0a849707559c80881e8,
unchanged Cargo.lock.

No new cryptographic key was generated or used to sign this new
Phase 4M replay. The previous REAL original signed bytes, public
host role manifest, exact original source-bound typed Decision and
typed price Outcome were reconstructed and checked against the
native Ed25519 verifier. Their ORIGINAL signature timestamps and
trust/scope/epoch were parsed from byte-exact canonical signed
messages; they are NOT re-created from a fresh key.

The original two actual test-only files were kept intact:
- ledger: SHA256
  cc1e79dec29099f67f7c58678c255090da6d8b2f7cbcc7ba0df59c5504d50e21
- witness: SHA256
  608f2719d1be86da03bebc214d7e881f793d49613cee01cd8824853b364b8108

They live in the original Phase 4L owner-private evidence folder.
Original trust and source identity were also checked. All Phase 4M
write actions occurred on **new independent copied files**, never
inside the earlier trial's original ledger and witness.

## Six separate actual original native SignedEvidence retry runs

Using the original full Canonical Cargo lock, the independent
Rust example real_source_native_replay.rs links the Phase4L
real_source_native_lab.rs helpers to the ORIGINAL native
LedgerWriter, DurableLedger, LedgerWitnessStore, signed Generator /
Observer verifier, and two stored original market events.

The original first compilation caught a test-only Rust2024 reserved
identifier; after renaming it, the qualified native test executable
built normally without modifying any original Canonical crate.
The final replay runner binary remains a private, noninstalled
test-only file.

Six final test copy folders under:
~/.local/state/heptatrader/operator-evidence/train-v2-native-replay-20261012/

| Scenario | Ledger initial | Witness initial | Native result |
|---|---:|---:|---|
| acknowledged | 2 | 2 | Replaying original Decision and Outcome with EXACT historical signing-byte evidence is IdempotentReplay, still 2 |
| expired | 2 | 2 | Trying an expired Generator evidence against actual current wall clock rejects, still 2 |
| lag-wrong | 2 | 1 | Old Decision or modified Outcome cannot advance a lag-one Witness, still 1 |
| lag-exact | 2 | 1 | Only byte-identical original, historically valid signed Outcome advances Witness from 1 to 2, still 2 events |
| lag-torn | 2 plus two incomplete trailing bytes | 1 | Original native recovery truncates only an incomplete final frame, then exact historical signed replay advances Witness to 2 |
| lag-expired | 2 | 1 | The exact previously signed Outcome fails authorization at CURRENT time after Observer expiration; Witness remains 1 |

**Critical safety interpretation:** for acknowledged, lag-exact and
lag-torn, historical-signature test replay uses the signed message's
original issued_at+1 timestamp to exercise native code on isolated
COPIES. That is a bounded historical-equivalence TEST, NOT permission
for a future production host to backdate wall time or override signer
validity. In actual production, both the source clock and issuer
revocation/expiration must be independently governed by trusted
host/Owner policy. The separate lag-expired native test confirms
that a CURRENT-time append returns an error, even when stored
historical bytes are exactly unchanged.

These are controlled file copies, NOT a real electric power outage.
Only a two-byte incomplete tail was introduced in the last scenario;
do not claim successful recovery from arbitrarily torn complete
records, hardware failure, controller cache loss or multiple
unacknowledged ledger events.

## Current-clock recovery admission guard (zero automatic writes)

New real_source_native_recovery_gate.py:
- Re-verifies all four archived, physical-host signed, original typed
  Ed25519 messages and actual previous native witness ACKs.
- Parses original signed Generator and Observer issued_at/expires_at
  and exact role, authority epoch, scope, objective and trust digest
  from canonical signing_bytes, not untrusted display timestamps.
- Requires the candidate files to be MODE 0600 under a named MODE
  0700 isolated parent, and byte-identical to the original qualified
  Ledger and either its full Witness or exact one-record Witness
  PREFIX. No caller-provided replacement root or transaction record.
- Uses the actual live Pocket4 system clock for this research
  decision. It is NOT an attested TPM/secure monotonic clock.
- Returns one of three statuses:

  ACKNOWLEDGED_NO_REAPPEND_REQUIRED:
    A complete original previously witnessed 2-record source; nothing
    is newly authorized merely because an original key is expired.

  REQUIRES_EXACT_VALID_SIGNATURE_OWNER_REVIEW_NO_AUTO_REPLAY:
    A lag-one witness and original Observer evidence still within
    declared validity. Still no automatic write, because a trusted
    product Owner must reconcile authoritative outcome and root
    revocation state; this branch is exercised only by synthetic unit
    tests, not inferred from the now expired real event.

  HOLD_EXPIRED_SIGNATURE_REQUIRES_INDEPENDENT_AUTHORIZED_RECOVERY_PROOF:
    A lag-one witness with exact historical public source evidence,
    but the original Observer signature no longer authorized at the
    actual current time. Never silently advance, backdate or resample.

The real Pocket4 actual-current-clock check returned ACK for four
already-witnessed/healed copies, and HOLD for the two deliberately
still-lagged copies. **None** changed bytes in either Ledger or
Witness, and no signer/key/broker API was invoked by the guard.
It is diagnostic/predeployment-only; it is NOT installed in the
active broker/learning host service.

The full new native signed retry+current-clock six-case
qualification receipt (private create-only, fsynced):
~/.local/state/heptatrader/operator-evidence/train-v2-native-replay-20261012/native-source-real-signature-ack-uncertainty-final-v1.json

SHA256:
d7d4a4ec1e59cea3dddb6b199ba43e84461383825f36a8e0cbf06201d2079d8a

## Production protocol requirements before this can be fixed

There is **no safe basis** here to call direct historical replay on
the product-owned writer after credential expiration. The correct
next product implementation requires an independent recovery Owner
with a formally pinned signer/root, trusted monotonic time,
current revocation cut and role separation, and an independently
DURABLY retained authenticated original decision/outcome intent
captured before or atomically with the first ledger commit.

The protocol must prove the exact canonical event and previous
ACK frontier from both the original ledger and a separately
retained original certificate or external witness, and only
authorize reconciling the **already-durable** single missing ACK.
No new event, unknown content, separate episode, policy change,
retroactive observed Outcome or resampling may be accepted. If
original signed proof or independent root control is absent,
the correct product outcome is HOLD with administrative
reconciliation, NOT a replacement row or fresh order.

A key rotation/revocation, correction, owner termination, forged
clock rollback and true power-cut simulation must eventually be
tested with these same rules. Merely passing two-file copy tests
does not qualify future product deployments.

## Still HOLD, not original trading authorization

The original opaque LLM's complete source-time candidate population
and actual sampled action propensities (including all HOLD and veto
paths), root-controlled production Generator/Observer registration,
actual broker order fills/commissions/slippage and net PnL,
product-eligible DatasetSnapshotV3, Artifact Owner/CURRENT,
independent unseen economic efficacy and physical power-cut survival
remain unqualified. The one real market price-bps test was NO_TRADE.

No new order, risk rule, live service, model inference, training,
production trust key, account or current artifact was changed.

## Final original-code and hostile-input test gates

The final replay-only Rust executable compiled from the test entrypoint
against the exact original unchanged Canonical workspace. It is not
installed in any service. Final native binary SHA256:

0261551a18be28958d36605ea831978595dd89ff13028b984539d532df42e7a6

The Phase 4M adversarial Python-only fixtures use newly generated
IN-MEMORY local test Ed25519 keys and SYNTHETIC dummy ledger shapes,
with original-market claims kept entirely separate. They cover:
- Exact native-signed role/digest/authority-epoch byte parsing.
- Public Ed25519 signature bitflip rejection and wrong role.
- Out-of-order / expired / malformed clock interval rejection.
- Identical previously ACKed source when the historic signer expired.
- Lagged witnessed source still within time validity -> owner review.
- Lagged witnessed source with current expired Observer -> HOLD.
- Original source copy and witness-prefix substitution rejection.
- Changed public signer verifying key and directory escape rejection.

17 new fixture tests and five fixture subtests passed. They do NOT
substitute synthetic ledger data for the actual six native Rust replay
experiments against the original frozen real-market source.

Full Python core + historical research:
**1,736 passed; 21 skipped; 750 subtests passed; zero failures**.

Python source test log SHA256:
82a519c9ef3242006c8614466b98fe0298c0ed7ac73117cb5f97526ee28ff2f6

The original **unchanged** Canonical hepta-learning-ledger and
hepta-learning-artifacts crate library regression:
**118 + 92 = 210 passed; zero failed; 2 explicitly ignored**.

Canonical Rust library log SHA256:
32c48d129fe636c4b45389c75bbeace60a977fbd6a7627c46cd4902aaf36ade0

Pinned original Cargo.lock SHA256:
8354b4cbdf14389db7aa5508e2ed5b524fc2d4c4ea0ecb475955c933f8bea389

Final complete six-scenario, exact original source identity and
Python+Rust regression qualification adds verification of the
retained original Phase 4L Ledger/Witness SHA256, tests both
already-ACKed and still-lagged copies, and records the true
current-clock signature-expiration HOLD.

Final privately fsynced create-only receipt:

~/.local/state/heptatrader/operator-evidence/train-v2-native-replay-20261012/native-source-real-signature-ack-uncertainty-final-v2.json

SHA256:
e1ef4ba2b60c1e504745a51d27366d9b9d4eb7b316cc2205d98688f41a064986

The earlier six-case receipt, also retained (before adding the
explicit full Python/Rust result fields), SHA256:
d7d4a4ec1e59cea3dddb6b199ba43e84461383825f36a8e0cbf06201d2079d8a

**Release remains
HOLD_AUTHORIZED_POST_EXPIRY_NATIVE_ACK_RECONCILIATION_AND_ORIGINAL_MODEL_PRODUCTION_TRUST.**
