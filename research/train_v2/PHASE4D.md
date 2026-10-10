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

In a separate pinned workspace, all **7,531 tracked native blobs** were
restored with zero modified `codex-rs` files, and all **193 original
Cargo workspace member manifests** passed offline no-dependency metadata
validation. Full upstream dependency resolution is separately blocked by
missing/offline third-party dependency metadata or source; the six-crate
qualification DOES NOT turn this unresolved original workspace into PASS.

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
