# Train V2 Phase 4D: original pinned native Rust library qualification

2026-10-11 on pocket4; stacked on Phase 4C. This is an isolated
implementation/host-qualification laboratory, not a production writer,
independent business observation, actual trade, artifact selection or promotion.

## Exact provenance and commands

Canonical upstream git commit: `78fdb0cf8537e3a84fc6e0a849707559c80881e8`.
The six original crates below were read only after verifying 158 pinned Git
blob preimages, source HEAD and absence of tracked modifications:

`hepta-types`, `hepta-intuition`, `hepta-cognitive-types`,
`hepta-memory-retrieval`, `hepta-learning-ledger`, and
`hepta-learning-artifacts`.

`native_qualification.py` builds an **isolated/generated six-crate Cargo
workspace** with unmodified original Rust files and a reduced dependency lock
whose third-party package versions and checksums must exactly match the
upstream `codex-rs/Cargo.lock`. This manifest is generated, not the
unmodified complete Canonical Cargo workspace. It first runs real native owner
tests, then the pinned locked offline LedgerWriter + Owner library tests. It
emits its receipt only after verifying the exact expected terminal test totals.

On pocket4:

- Python harness regression: 12 passed.
- Original native Rust libraries: **92 + 118 = 210 passed**, 0 failed, 2 ignored.
- Real original target-host executable: 256 signed test Decisions persisted
  through `LedgerWriter` into 8 on-disk segments with a separate witness;
  256/256 exactly recovered on reopening; source commit and tree verified.
- Host measurement: reopen 17,851 microseconds; sustained 143.65 appends/s;
  177,220 bytes stored; RSS increased 516 KiB.
- The host executable uses **hard-coded test root and generator keys** and
  test-only deterministic candidate selection, NOT host-enrolled product keys
  or genuine original trader action probabilities.

Reproduce the source-library run (choose a NEW, empty output directory):

```sh
cd /home/alex/work/heptatrader-train-v2-native-ledger-qualification-20261010
PYTHONPATH=research python3 -m pytest -q tests/python/test_train_v2_native_qualification.py
PYTHONPATH=research python3 -m train_v2.native_qualification \
  --native-checkout /home/alex/work/hepta-trader-neuron-native \
  --output /home/alex/.local/state/heptatrader/operator-evidence/NEW-EMPTY-QUALIFICATION-ROOT \
  --timeout-seconds 600
```

## Retained immutable evidence

Private Pocket4 evidence root:
`~/.local/state/heptatrader/operator-evidence/train-v2-native-ledger-cargo-pinned-v2-20261011/`

- `evidence/native-qualification.json`: SHA256
  `50e08f17608e1a8425b892b3abea883ab9ca9163d328cbcb50abd95906bcb639`.
- `evidence/native-both-locked.log`: SHA256
  `e1833de92ec96550bcfd108d731c6138cd67d8644751d6fd4983112a4eb1c1ea`.
- `evidence/target-host-measurement.json`: SHA256
  `485a76448d34a0ade41183e627fda984747a2a9fdb656f054ae66c6c1a5eaa72`.
- Exact upstream `codex-rs/Cargo.lock`: SHA256
  `8354b4cbdf14389db7aa5508e2ed5b524fc2d4c4ea0ecb475955c933f8bea389`.

A second **full original Git working tree** was restored on Pocket4 at
/home/alex/work/hepta-canonical-full-ledger-p0-20261010. All **8,665 tracked
repository files** match the exact Canonical commit
78fdb0cf8537e3a84fc6e0a849707559c80881e8 with zero modified files.
The original Cargo.lock SHA256 stays
8354b4cbdf14389db7aa5508e2ed5b524fc2d4c4ea0ecb475955c933f8bea389.

**2026-10-11 full original dependency closure, not an isolated subworkspace:**

- Original Cargo metadata ran with --offline --locked --format-version 1:
  **193 workspace members / 1,517 fully resolved locked packages**.
  JSON evidence SHA256:
  68820a5c211683daee66d85e0fa37e6e548fc9bd6ba290cfa107d997a4a5280e.
- All **1,316 crates.io .crate archive SHA256 hashes** were independently
  checked against original Cargo.lock: 1,316 pass, zero missing or mismatch.
  Integrity receipt SHA256:
  0641c39bdb7a5415cf34261ee5072398ad9cfe02ebf60f47c12f3e562c113919.
- The missing pinned v8-150.4.0 archive was reconstructed from verified
  byte ranges and accepted only with its exact original lock SHA256.
  Range-recovery receipt SHA256:
  df2458890d9a1ebfb7b641223a2a81897c9ed14901db6dd815b78e8e88aebb9c.
- The unmodified original full workspace ran the genuine
  codex-hepta-learning-ledger plus codex-hepta-learning-artifacts library
  suite under --offline --locked: **92 + 118 = 210 passed; 0 failed;
  2 ignored**. Test log SHA256:
  41a47cf7bd39ba23c37106f8682424fc23d8812f446f213468eb42245e414216.
- Separately, four actual Rust process-crash, witness/reopen, and
  exact retry regressions passed in the isolated harness. Receipt SHA256:
  ff0a0ec11ea8039cd8cdaac0d9ebffeb086ee6221ea3908588c1b48a5e9dd862.

Complete original-workspace evidence root:
~/.local/state/heptatrader/operator-evidence/train-v2-full-canonical-20261011/

This **closes exact source and pinned dependency resolution plus the two
original native library tests**. It does NOT assert that all 193 members
were built/tested, that the local witness survived genuine power failure,
or that production keys, product Trader decision calls, independent Outcome
issuers, dataset final use, or artifact Owner/CURRENT have been admitted.

## Strictly unclosed product proof

Do not substitute these qualification keys, test probabilities, and synthetic
decisions for the original PAPER trader Decision/PREPARED identities or
for an independently administered root-signed identity distribution.

Genuine original complete candidate sets; logged behavior-policy selection
propensity (never model confidence); product Generator and independent Observer
controller/credential/key custody; source-bound authenticated terminal outcomes,
corrections and revocation; a host-owned persistent witnessed LedgerWriter; native
V3 dataset freeze plus current-use revalidation; a separately accepted artifact
Owner/CURRENT head; and new unseen prospective after-cost observations are all
still mandatory. Real unexpected power loss remains unqualified. The existing
Phase 4C 12-item HOLD remains the production gate. No order, broker query,
model update, selection, deployment or driver/service restart occurred.
