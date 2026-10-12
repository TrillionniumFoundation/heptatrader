# Train V2 Phase 4S — Native source-time signing deadline audit

2026-10-12. Isolated Pocket4 research branch stacked on Phase 4R
(d577acb9a9a2198fe1832266b10066d779eca23c, PR #210).
No live PAPER broker API, service, old Trial3, model, Laya admission,
private keys, Artifact Owner, native original LedgerWriter or previous
witness history has been modified.

## Why previous "signed before outcome" is not enough

Phase 4L is a REAL original EUR/USD PAPER source-based NO-ORDER shadow
decision, signed by Mac with a TEST-ONLY Ed25519 Generator and durably
appended to the unmodified native Canonical LedgerWriter before the
five-minute price result. X230 later independently observed and signed
the PRICE outcome, and the original Writer independently witnessed it.
Those are real research, cross-host, native-durability facts.

But the original shadow source's IB-reported TTL is five seconds.
The earlier native write occurred AFTER that source's own quote expiry,
and no original trader REQUEST-to-PREPARED identity was proven. It must
not be classified as a native authorized pre-trade decision.

## Actual source and native times from untouched original bytes

Exact frozen private Phase4L root:
~/.local/state/heptatrader/operator-evidence/train-v2-real-source-native-20261012/

At Pocket4 +08:
- Original observed quote: 2026-10-12 09:16:59.984.
- Actual local quote available: 09:17:00.389.
- Shadow NO_TRADE sample selected: 09:17:01.699, before expiry.
- Original IB quote expired: **09:17:04.984**.
- Canonical signed Generator native issued_at: 09:17:14.437.
- Separate Mac host reported completion of Ed25519 Generator signature:
  **09:17:14.660**, after quote expiry by **9,676ms**.
- Original native LedgerWriter/Witness test ACK: **09:17:14.922**,
  after quote expiry by **9,938ms**.
- Five-minute PRICE observation was only due at 09:22:01.699; its
  independent later signature and witnessed result remain intact.

The original signed native issued_at and independent host signed_at_utc
are consistent within 223ms; neither is a trusted TPM/monotonic clock.
The original xhost role verifier rechecks all FOUR Ed25519 signed
messages, exact role/domain, original shadow packet and native ACKs.
The actual IB PAPER source reparser rechecks the hash-chained quote file
(901 original records on this readback), event availability and the
original stale_after_ms, not a recomputed or caller-extended TTL.

Three original private source hashes are PINNED before acceptance:
- immutable NO_TRADE shadow decision SHA256
  0ec5ffc1e73158305916af6cd96cc68a76b73bd2d3f0f6727e0ed5eb66a87fb4
- original root+Mac public signature transcript SHA256
  4c28088d53484be3d95c8f3cb3e638a146f9f6f709e261701adc6f5a56759023
- original local native Decision/Witness ACK receipt SHA256
  62e2014cfcfa5b56e0b5e0cb558f6837450e036dfc144382f5a917c34b83a6d0

Even a rehashed local copy of a backdated ACK cannot become a new
source-time fact: the frozen byte identity is an explicit input boundary.
A local ACK receipt is still NOT a product-independent hardware clock.

## Implemented Phase 4S code

- native_preaction_timeline.py invokes the existing ORIGINAL public
  Ed25519 verifier, decodes native SignedLearningEvidenceV1 time from
  its signed byte layout, checks issuer/remote claimed signing time
  consistency, original quote chain + period and original native ACK.
  Its stateless future-record chronology enforces source event/known/
  expiry bounds, sign/ACK chronology, exact 300s horizon and original
  PREPARED deadline if and only if separately provided.
- native_preaction_tamper_smoke.py copies selected original immutable
  receipts into private 0700 temporary folders and rechecks six
  deliberate local forgeries. Only COPIES are edited; all old files and
  actual native Ledger/Witness remain byte-identical and in place.
- test_native_preaction_timeline.py covers genuine numeric original
  deadline counterexample, hypothetically valid time order, malformed
  native signing fields, bool timestamps, late signer, late ACK, late
  source and lack of original behavior propensity or PREPARED.

This cannot be used to generate or verify a product RecoveryGrant,
mint a product Root, synthesize a missing original REQUEST/PREPARED,
supply original LLM propensity, commit a real order or promote a model.
A deliberately plausible pure timestamp vector does not prove role
independence, actual event timing or product root enrollment.

## Actual final refusal evidence

Actual original signed native source:
HOLD_ORIGINAL_NATIVE_SIGNATURE_AND_WITNESS_TOO_LATE_FOR_QUOTE.

First unmodified test-only read-only audit SHA256:
ece71666b7076e9b69b262dd596d6555fc9c1ff1b6c2e4e00783c8a722aa56e5

Code-bound and original fixed byte PINNED read-only receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-phase4s-native-time-20261012/original-phase4l-native-deadline-pinned-v3.json

SHA256:
ca62c91e7caeca695063729bf29659dcdce48254c23f60b2ffa342305c9a12cf

Six private-copy hostile cases were all rejected: backdated native ACK,
backdated Mac displayed signing time, extended historical quote TTL,
false original LLM propensity, invented original PREPARED eligibility,
and substitution of original source segment. No old file overwritten.

Private hostile-copy proof SHA256:
61090e2362cae151dc1c484816888d210053b323165da3520f17327bfcad5587

## Mandatory new prospective product gate

For a future genuine product pre-trade event, require all of:
(1) original quote locally known and not expired at decision and signing;
(2) authenticated native Generator signed bytes and independently timed
durable witness ACK *before* original broker PREPARED, not just horizon;
(3) original full BUY/SELL/HOLD/ABSTAIN/veto/no-model decision population
with the actual behavior-policy sampling probability and all hard gates;
(4) independently root-enrolled generator, observer, correction/evaluator
and RecoveryAuthority controllers; current revocation/antirollback trust;
(5) actual signed native DatasetSnapshotV3 and CURRENT Artifact Owner
with previously unseen, post-cost financial efficacy evidence.

Source expiration or already-missed native PREPARED cannot be repaired
by a later synthetic escrow, fresh Ed25519 certificate, different host,
old-signed-event timestamp replay, revised IB historical bars, or
later successful LedgerWriter recovery.

This is a stricter acceptance rule for FUTURE candidate experiments,
not a change to the original five-minute PRICE study or a claim that
its signed native records are incorrect. The original study remains
valid within its explicitly test-only, preoutcome, PRICE-only scope.

Exact source, native original and Python test logs must be retained;
no product merge or deployment without independent exact-commit CI and
host acceptance.

## Exact-source final regression (Pocket4, 2026-10-12)

- Focused native/preaction timestamp and adversarial syntax tests:
  22 passing, zero failing.
- Full Python pytest across tests/python and historical-training tests:
  **1,855 passing, 23 preexisting/optional skipped, 793 passing subtests**,
  zero failures.
- Source/build/documentation Python lane: **106 passing**, zero failures.
- Unmodified pinned original canonical Rust learning-ledger:
  **118 passing, one original ignored**.
- Test-only native Phase 4R bytechecking bridge compiled with the same
  unchanged original Cargo source; its zero standalone tests passed.
- Six private COPIES of old signed source evidence all failed on hostile
  fabricated ACK/signature time, TTL, propensity, PREPARED and file path.
  Original two native ledger/witness data files and all root/observer
  signatures remain untouched.

The copied original source / actual remote role attestations are not a
sufficient secure-monotonic product timestamp. Phase4S deliberately
reports stronger TIME-based HOLD, not a newly authorized old action.

Phase4S private hostile-copy immutable receipt SHA256:
61090e2362cae151dc1c484816888d210053b323165da3520f17327bfcad5587

All local qualification logs are under:
~/.local/state/heptatrader/operator-evidence/train-v2-phase4s-native-time-20261012/


## Final born-private hostile copies and exact source integrity

The hostile-copy fixture was further hardened so every copied original
receipt is opened with O_CREAT|O_EXCL|O_NOFOLLOW and explicit mode 0600
AT BIRTH; chmod-after-creation is not treated as equivalent. The
temporary parent remains owned 0700, all copies are removed on exit,
and the original immutable source, native Ledger and Witness remain
unchanged.

All six attacks were independently refused on the final copied-source
implementation. The new create-only, file+directory fsynced receipt
includes SHA256 of the exact adversarial script AND the original
timeline auditor source.

Final copy-proof receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-phase4s-native-time-20261012/private-copy-hardened-final.json

SHA256:
7da2410f4a25c220dd3ff7b5c4107c953ef5fc6071f34c49df0712dae35a9080

The actual Phase4L native generator and original 2-event PRICE outcome
remain valid TEST-ONLY experimental evidence. This timing HOLD is an
additional source-time/product-admission requirement, not a claim that
the native cryptographic signatures were invalid.
