# Train V2 Phase 4C: exact Canonical LearningLedger readiness audit

2026-10-10, pocket4. Research-only, based on Phase 4B a6d020a.
The live PAPER FX/BTC strategy, OMS, active Execution, existing Trial3,
source-health HOLD, old experiments and broker account are not changed.

## Actual upstream contract reviewed

The frozen real hepta-private-ci native source is commit
78fdb0cf8537e3a84fc6e0a849707559c80881e8. This patch inspects
its unmodified production.rs, model.rs, causal_v2.rs, signed_evidence.rs,
trust_distribution.rs, witness.rs, dataset_receipt_v3.rs and the canonical
hepta-learning-artifacts owner_host.rs.

The native Product LedgerWriter does NOT accept the old Train V2 research
JSON event as a decision. Its ProductionDecisionV2 has typed candidate set,
selected candidate and selected_propensity; completeness must be proven by
the original generator with zero omitted candidates. A remote model's
confidence is a model output and is NOT its actual stochastic behavior
selection propensity.

The canonical writer requires independently authenticated generator and
observer controllers, root-activated learning trust, signed V2 decision and
outcome facts, durable LedgerWriter and independent witness ACK. Its
DatasetSnapshotReceiptV3 freezes active source and correction/revocation
cuts from authoritative ledger replay. An ArtifactOwner trust/withdrawal
CURRENT head and independent selector are additional separate requirements.
Reusing the local SHA256 or operator-controlled Ed25519 research witness
as any of these typed product proofs would be invalid.

## What this branch implements

- canonical_native.py: fail-closed source layout scanner of exact local
  git HEAD plus clean tracked bytes of the real pinned Rust APIs. It parses
  the actual field names in three product structs and checks five writer
  entrypoints, durable witness, dataset snapshot and artifact verifier.
  SHA256 of each original Rust source file is preserved in the receipt.
- canonical_admission.py: rechecks actual local PAPER original source
  quote-chain, exact scoped quote, genuine as-of feature, causal cutoff
  and pre-outcome status. Emits an explicit 12-condition HOLD, not a
  synthetic native decision. A scalar model confidence cannot be promoted
  into native selected_propensity. Self-declared native successes or fake
  Owner/ledger attributes are rejected even when rehashed locally.
- canonical_smoke.py: bounded read-only audit against the preserved
  original Laya PAPER quote segment and local pinned native code. It
  writes a new immutable private evidence receipt with 0 broker requests,
  0 orders and no model update.
- tests/python/test_train_v2_canonical_readiness.py: negative tests
  for source tampering, fake product authority, propensity/confidence
  distinction, expired source, wrong native version, missing Rust fields,
  absent methods/witness, tracked source modifications and symlinks.

The frozen Pocket4 native source repository is a deliberately partial
copy: hepta-intuition and hepta-memory-retrieval Cargo package directories
are missing. Their absence is captured as an explicit negative dependency
flag. This work DID NOT compile, execute or write a native LedgerWriter,
did not activate product root trust and did not publish an artifact.
Synthetic Rust fragments in focused tests exercise the parser; they are
NOT claimed as native product source or build evidence. Only the separate
real-source smoke attests actual local pinned original source shape.

## Real source and strict current gate

Original PAPER hash chain: 678 locally verified quotes. The new V2
source-expiry profile can reconstruct a past original causal feature.
Current assessment: HOLD_MISSING_NATIVE_LEDGER_WRITER_AND_OWNER_FACTS,
12 unmet independent native/economic requirements. Historical replay of
a quote is not a new forward prediction; no new signed product decision
or broker query was made. Current all-in account PnL remains unknown
due to the missing qualified EUR/USD mark.

Private receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-canonical-readiness-20261010/source-audit-v1.json

Initial receipt SHA256:
4270d3523ba270175c365ba04ce0b3430837933b02c71f8079a19d7c7d463adf

## Next real product Host gates

1. Obtain a complete, exact version-pinned canonical Native workspace and
   compile/run the original LedgerWriter, durable witness and independent
   trust/rotation/recovery tests without replacing dependencies.
2. Enroll genuine product-root trust with distinct generator and observer
   principal/controller/credential chains and independently protected keys.
3. Bind the true remote behavior action enumeration and actual policy
   propensity to the original Decision/PREPARED identities. Missing logged
   propensities cannot be inferred from confidence, price labels, or closed
   trade outcomes. Deterministic policies must explicitly prove their
   decision support and eligible no-change alternatives.
4. Through actual LedgerWriter append authenticated decision/outcome with
   independent retained witness and corrections, freeze the actual V3
   dataset snapshot and revalidate before final use.
5. Only then, through the real Artifact Owner/withdrawal CURRENT and
   independent evaluation, consider a separate operator-approved PAPER
   strategy trial. Do not reinterpret old windows as unseen.

This module creates no positive native/canonical acceptance path.

## Final pinned actual-source receipt and regressions

Final immutable receipt after source-Python SHA binding:
~/.local/state/heptatrader/operator-evidence/train-v2-canonical-readiness-20261010/source-audit-v2.json

SHA256: 51ac7f4baca5a7eb55b31306d8de56528fa141420d67e3a956f178d667658a9b

- Dedicated Canonical negative/contract tests: 17 passed.
- Full historical research suite: 400 passed, 7 existing skips.
- Full core Python suite: 1,860 passed, 21 existing skips.
- Source/ownership/documentation partition: 106 passed.
- Original locally hashed PAPER chain: 678 observed quotes.
- Canonical source commit: 78fdb0cf8537e3a84fc6e0a849707559c80881e8.
- Real native Rust source layout hashes included; full pinned workspace absent.
- 12 mandatory original-policy, authority, owner, durability and economic
  requirements remain MISSING. No pretend candidate, ACK, new signed
  event, inference job, broker request or order was created.

Even with an exact source-layout readback, this is NOT a successful Rust
LedgerWriter build or host-enrolled test. Do not merge or promote it as
a production activation, independent trust or release admission.
