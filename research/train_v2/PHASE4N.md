# Phase 4N — independent source-time Escrow candidate, product HOLD

2026-10-12. Draft-only, staged on Phase4M. No new market-source
decision or Outcome was claimed, no order or broker query, no product
Root key, original trading strategy update, product ledger service
deployment, training, artifact or model promotion.

## Original problem

Phase4M proved with REAL existing IB PAPER source-backed, test-only
Mac Generator and X230 Observer Ed25519 messages that a complete
Ledger entry can remain one record ahead of the separately durable
Witness. Native exact historical retry can repair a ONE-record
unwitnessed tail without new Ledger events, but the same signed
Outcome is rejected at CURRENT time after its issuer expired.
Backdating caller time is not a legitimate product recovery policy.
Therefore product authorization remains HOLD.

## This new implementation

The Phase4L Rust example now has a separate preappend Escrow gate
at BOTH native append stages, without any original Canonical crate
change. It sends a strict canonical ESCROW_REQUEST after the native
Generator or Observer SignedEvidence exists but before calling the
original native LedgerWriter append method. The request binds:

- original typed signing payload digest and detached native signature
- original source quote / shadow policy and exact source packet digest
- prior independent Witness anchor and intended next Ledger sequence
- predecessor chain digest and issued/signed time
- explicit research-only and non-product flags

No external Escrow response means the original test entrypoint
cannot proceed to native append. A received response is checked by
the Rust Ed25519 verifier under a pinned test public key, exact
receipt digest, stage, issuer host claim, role, time window and
identical event. Local receipt uses exclusive creation and fsync.

The independent host candidate escrow script binds DECISION custody
to X230, distinct from Mac Generator, and OUTCOME custody to Mac,
distinct from X230 Observer. Its owner-private 0700 root / 0600 key
and receipt files are explicit. The host validates native JSON,
predecessor/witness count, source-time purpose and root denial, and
fsyncs a duplicate-safe signed intent archive before replying.

These keys and host roles are TEST ONLY and are not a root-approved
independent product RecoveryAuthority. A hostname string or a
self-pinned public key does not establish hardware custody,
independent enterprise administration, or durable remote fsync.
A future transport must independently read back the origin-host
receipt and verify full product root credential enrollment.

## Actual test evidence and failures

The Rust Escrow preappend gate compiled against the original clean
Canonical hepta-private-ci 78fdb0cf8537e3a84fc6e0a849707559c80881e8
with original offline locked Cargo sources. The test-only example
file was removed from the original workspace after compilation.

A SECOND original-Cargo-linked Rust example was actually invoked
by Python subprocess tests with clearly SYNTHETIC in-memory test
Ed25519 keys and synthetic signed event bytes (NOT real broker
records or physical Mac/X230 private keys).

Tests:
- genuine Ed25519 test receipt validates for decision and outcome
- missing Escrow ACK refuses before Ledger/Witness creation
- signed message bitflip, wrong original quote, role, host claim,
  public key, fsync flag, forged future timestamp are rejected
- successful synthetic Escrow creates ONLY a local 0600 receipt,
  without any Ledger/Witness append
- the positive synthetic test intentionally proves that host name
  metadata is self-assertable. This is a release blocker, not
  evidence of actual independent physical custody.

Separate Python Escrow tests exercise duplicate immutable intent
failure, signature verification after temporary TEST key removal,
different-host role blocking, private modes and bounded hashes.

Two real correctness bugs were found/fixed in NEW test source:
1. Python boolean True was accepted as integer witness next=1;
   now previous and next counters require exact int types.
2. The Python Escrow test helpers mutated process-global umask,
   breaking an earlier directory-permission adversarial regression.
   New code creates explicit 0700/0600 files without changing
   library caller umask. Original failing test output is preserved.

## Genuinely registered new live plan NOT consumed

Pocket4 locally and immutably pre-registered a new REAL original IB
PAPER EUR/USD shadow research plan at 2026-10-12 10:14:26.252 +08:

Plan ID:
f9a2edd0fe7721e56d9e26da328c0f3750f54d223a20418cb2cda78606fe07b4

First permitted decision 10:15:51.252, final window end
10:29:26.252 +08 (initial registration lead ~85 seconds).
Because the independent host Escrow and its actual remotely fsynced
receipts were NOT successfully enrolled/attested through an
authorized channel, this preregistered plan was deliberately
NOT used for a new Decision/Outcome/ledger write. No backdating or
manufactured new source-time signed result is allowed.

The real_source_native_escrow_eligibility diagnostic returns:
HOLD_INDEPENDENT_REMOTE_DURABLE_ESCROW_NOT_ATTESTED_OR_PRODUCT_ROOT_ENROLLED.
It continues to HOLD even if unauthenticated local files falsely
claim independent Escrow and Writer completion.

## Real test results and remaining production requirements

The new targeted Rust+Python suite: 33 passed, 5 subtests passed.
The first complete Python regression correctly failed at a
pre-existing privacy directory-permission assertion because of the
new global umask pollution; it is retained as a failure.

After exact umask isolation repair, complete HeptaTrader core +
historical research: 1,769 passed, 21 skipped, 755 subtests passed,
0 failed. Final log SHA256:
92ddd495400ecf65094c7bccb60137440c6ead63096c724c405239eecd6dec6d

One original-Cargo-linked native TEST-ONLY Escrow contract example
was successfully compiled. No claim of fresh full-Cargo original
library test rerun is made for this new branch beyond the preceding
Phase4M qualification (original unchanged native crates).
Production product signer/root has NOT been enrolled.

To close the actual product gate, product design must introduce
a reviewed RecoveryAuthority role/type and root-signed enrollment
(current Canonical LearningEvidenceRoleV1 lacks it), trusted
monotonic time, independently administered custody of original
preappend signed intent, current root/revocation checks, exact
one-record committed-but-unwitnessed reconciliation and a separate
signed one-time RecoveryGrant. The grant must authorize ONLY
witness ACK catch-up for the SAME already-durable event, never a
new Outcome, clock rollback or overwritten history. It must survive
real power interruption and be tested for duplicate grants,
host/key substitution, corruption and independent escrow outage.
Absent any proof the only outcome is HOLD.

The original opaque LLM action propensities and complete action
population, genuine broker fills and cost-complete PnL, market-eligible
DatasetSnapshotV3, Artifact Owner/CURRENT and unseen efficacy remain
separate HOLD gates. Do not merge/deploy this Draft as production.
