# Train V2 Phase 4R - complete physical escrow bytes through ORIGINAL native Rust

October 12, 2026. Isolated research branch based on Phase 4Q (184adfc).
No live trading, Laya admission, original Trial3, broker API, native production
ledger, durable Witness or Artifact Owner was changed.

## Exact defect found in prior TEST-only Phase 4Q source

Phase 4Q correctly signed and durably escrowed synthetic complete bytes on
physical X230 and Mac. But the Python generator constructed three original
SignedLearningEvidenceV1 digest slots in this order:

    TEST_SCOPE, TEST_OBJECTIVE, TEST_TRUST

The real pinned Canonical Rust API at hepta-private-ci commit
78fdb0cf8537e3a84fc6e0a849707559c80881e8 signs:

    trust_digest, scope_digest, objective_digest

Therefore the old two physical archives are valid Ed25519 messages and
physically recoverable bytes, but their operator-intended field semantics
are wrong. The original native reparser correctly rejects both with
ORIGINAL_TEST_DIGEST_SEMANTICS_DRIFT. **Old files are never overwritten,
deleted, reissued, fixed, or promoted to a native LedgerWriter event.**

## Implementation

- physical_full_escrow_orchestrate.py creates corrected trust/scope/objective
  order ONLY for NEW synthetic messages. The receiver's archive/readback
  rules remain unchanged so old historically signed bytes are still readable.
- physical_native_reparse.rs uses the ORIGINAL unmodified
  codex_hepta_learning_ledger::SignedLearningEvidenceV1 Rust type, actual
  signing_bytes(), Digest32 and StableId. It reconstructs exact native fields
  from the complete original stored message, requires the intended TEST
  digest semantics, validates exact payload SHA, actor Ed25519 signature,
  original synthetic test role, physical receipt role/host/scope and the
  independently signed physical-host Ed25519 receipt.
- physical_native_build.py copies five real, SHA-checked original Rust
  crates from the PINNED commit to a separate minimal Cargo workspace and
  builds the test-only Rust binary offline. This is not a full Agentd build,
  production Trust activation or native LedgerWriter append.
- physical_native_roundtrip_qualification.py first READS prior X230/Mac
  complete archives and confirms the native semantic rejection; then
  creates one fresh SYNTHETIC signed payload for each host, verifies
  separate physical file+directory fsync and independent SSH readbacks,
  checks exact native Rust roundtrip, loses the ACK and verifies exact
  original-byte idempotent retry, and executes eight negative Rust tests
  per host. Private actor keys exist only transiently in the source process.
- physical_native_readonly_recheck.py uses only physical SSH READ mode on
  the ALREADY archived old/new byte pairs, and verifies both the old
  rejected semantic order and the new correct native typed signed bytes
  with the FINAL rustfmt-formatted Rust executable. It issues zero new
  sign requests, writes zero new physical files and does not change ACKs.
- tests/python/test_train_v2_physical_native_roundtrip.py adds exact
  digest-order regression, product-payload discrimination, a Rust positive
  reparser test and a true Ed25519-signed wrong-order native rejection.

## Original source and physical host custody

The proper pinned SSH aliases with existing verified host-key identities
are on qian-qi (not pocket4). The first attempt to use x230-ts from
Pocket4 safely refused name resolution before any physical write.
We did NOT add unknown SSH keys, relax StrictHostKeyChecking, reconfigure
Tailscale DNS or copy SSH credentials between hosts.

The existing qian-qi SSH profiles were used to reach:
- X230: qian-ThinkPad-X230, synthetic generator/decision evidence.
- Mac: MacBook-Pro.local, synthetic observer/outcome evidence.

The exact pinned original Canonical Rust executable was compiled on pocket4,
then independently SHA-verified after transfer to qian-qi. The executable
does not open a broker socket or trust/signing key. It accepts a bounded
test-only JSON envelope on stdin and prints only digests/verdicts.

Prior Phase4Q archived old originals remain byte-identical:
- X230 old SHA256 a4ddc4a72b389eb6435b08d3e4c2dc4e37db31e6b12e01ef2591516c091cb481
- Mac old SHA256 147d8b5c109bbd4f5935950000387ff83435a69156e4b0d616fa532e540e84e2

New, corrected synthetic complete archived originals:
- X230 SHA256 10730d189e360d2559af481a5fe8bcf159091db2c4d0fb6989986f9e4065ba4f
- Mac SHA256 f68fe3b25baf3668b052c74b1a943a51b1ed58537b8351980a47aa94ebc35b87

Each has a separate actual physical host-signed escrow receipt, fsynced file
and directory, strict readback and idempotent same-byte lost-ACK retry.
Test-only newly generated roles are under one operator's control, not
independent product Root or hardware trust.

## Code-final native Rust exact source and test proof

Final formatted bridge Rust source SHA256:
8ffd5e532772102dd2bec09c797db7c4dfbd1a8ed68e92ce383b09d62dc65eb0

Final native executable SHA256:
3d315c51b2592eee4b5a1435f97ce956bb85feb9d59a5865b58e18eac2aeaf74

A minimal source-copied offline Cargo workspace builds the exact original
signed-evidence type. Native original ledger tests passed 118 (1 existing
ignored); cargo clippy -D warnings and cargo fmt --check passed. The final
build receipt was created mode0600 with fsync from birth. The FIRST build
receipt was created with inherited mode0664 inside private mode0700
directory and then corrected to mode0600; it is not claimed private-from-birth.
No actual secrets or production signed evidence were in the build receipts.

The initial real two-host native proof (includes one NEW pair on each host)
was immutable O_EXCL/fsync sealed at Pocket4:
~/.local/state/heptatrader/operator-evidence/train-v2-phase4r-20261012/phase4r-two-host-native-report.json

SHA256:
a94b4366ea4a266807e5f1d5577fe4d27df447d357c40b2602c55fdaf192c038

The independent FINAL formatted-native-binary READ-ONLY physical proof is:
~/.local/state/heptatrader/operator-evidence/train-v2-phase4r-20261012/phase4r-twohost-codefinal-readonly.json

SHA256:
c8bb2f344714303d03f011be67e696dbe3ad56201f8d47ba40d1b777fa21aa30

The final read-only run reverified old rejection + new acceptance on both
physical machines, and separately refused all 16 challenged tamper cases.
It created **zero additional physical signatures and zero remote files**.
The two proof copies on pocket4 were O_EXCL-style create-only, final
permissions 0600 and their exact SHA hashes and containing directory
were separately fsynced.

## Mandatory HOLD / next release gate

Do NOT claim that synthetic payload is native ProductionDecisionV2 or
AuthenticatedOutcomeV2. It is not. Merely constructing a native signed
evidence envelope does not activate product root, trust generation,
distinct independent controller authorities, complete candidate/action
sets, genuine behavior-policy propensity or original IB request/PREPARED.

This branch never calls LedgerWriter::append_decision, append_outcome,
freeze_dataset or WitnessStore::advance. The separate feature-gated
Canonical ACK-only recovery research cannot be applied to these
synthetic archives without an externally authorized Root/RecoveryGrant,
true source-time preappend original escrow and a pre-existing durable
native event. Production remains HOLD. No complete NAV/PnL or unseen
economic alpha is claimed.

True next acceptance is: authoritative original policy candidates and
logged propensity, product-owned independently enrolled controllers,
fresh actual market Decision signed and original typed payload escrowed
BEFORE original native append, root-authorized independently witnessed
Outcome, no unknown ACK replay, DatasetSnapshotV3 and signed owner CURRENT,
and a new independent after-cost economic validation. Old failed windows
must never be retroactively reclassified as successful.


## Exact final source-tree regression

After fixing only NEW synthetic test message generation, formatting the
original Rust reparser, rebuilding in the source-hashed isolated Cargo
workspace and completing the final SSH READ-ONLY recheck, these exact tests
passed on Pocket4:

- Full pytest tests/python plus historical: **1,835 passing, 21 existing
  skipped, 778 passing subtests**, zero failures.
- Source/build/docs Python lane: **106 passing**, zero failures.
- Focused original native-format regressions: **6 passing**, including
  two actual Rust-native tests via the canonical pinned compiled bridge.
- Original Canonical learning-ledger (copied byte-identical source):
  **118 passing, one original ignored**; newly added native bridge
  executes without requiring real product owner identities.
- Native bridge cargo clippy --all-targets -D warnings: PASS.
- Native bridge cargo fmt --all --check: PASS.
- Source Git tree did not alter Canonical original Rust files,
  product Root distribution, LedgerWriter, native Witness or broker account.

All private test logs and copied original build inventory live under
Pocket4 ~/.local/state/heptatrader/operator-evidence/train-v2-phase4r-20261012/.

The user-owned operator at qian-qi controls BOTH participating synthetic
tests and the SSH credentials. Two distinct physical signed receipt keys
are not product-independent administrations. Native Rust byte correctness
is not native product ledger acceptance.

The separate latest Pocket4 production check remains mandatory and cannot
be inferred from regression success. No historical incomplete Escrow or
previously unknown outcome was silently upgraded.
