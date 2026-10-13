# Train V2 — independent single-pass cross-host custody for frozen 3×4 PRICE cohort

**2026-10-13 · Pocket4 + qian-qi · isolated Draft candidate, stacked on PR #281.**

## Goal and exact original contract

The earlier qian-qi standard-library cross-host audit independently validated a **fixed, frozen** Pocket4 original PAPER source across three disjoint 5/15/30/60-minute price windows. It verified the complete 285 naturally ended quote segments (8 archive pages, 137,642 quote receipt identities, 214,194,780 bytes), but read and validated those same source bytes **three times**. Actual wall time was 75,131.281 ms. Existing 269-segment frozen prefix hash, original 3 source decisions, 12 price outcome files, the oracle SHA and all old failures remain immutable.

Original independent frozen 3×4 receipt SHA256:

`294bba1c272a23a0f27afe3dddfa1e0c20f9a6958c47bcdac0a24e362d86e3c7`

Original first-269 segment SHA256:

`cd8540b689d4ae75d76ec34fe0e6f72a06a0df729b8dd8d4a12cd13f5a5658db`

## What actually changed

Two new **portable Python standard-library-only** modules:
- `research/paged_crosshost_singlepass.py` — does a SINGLE complete read/validation of every copied original numeric segment, original full-chain event, original source quote identity, and all archived page+ledger receipt hashes. It projects the original **arrival-time-selected** quotes into exactly three bounded and pairwise-disjoint windows. It refuses unbounded windows, overlapping windows, source duplicates, corruption anywhere outside selected windows, changed archived source and mutated prices. It does not skip early or out-of-window source files.
- `research/train_v2/independent_frozen_12cell_singlepass.py` — uses the unchanged and pinned prior independent `paged_crosshost_custody.py`, prior frozen price verifier, and all original immutable campaign/input/timer/outcome receipts. It runs the single-scan reader once, calls the existing independent Decimal price/horizon validator for each window and requires exact equality of each original per-window result to the prior three-scan report. It also verifies all old frontier/quote-count/source-byte identities before calling the result qualified.

The existing original independent parser and original old 3-scan receipt were **not modified or replaced**. New code uses neither Pocket4's authoritative Train V2 source parser nor any broker API or market transaction channel.

## Real qian-qi scan and independent exact readback

New package-importable code was run on qian-qi against the actual original 208 MiB copied source; outcome:

| Actual measurement | Original triple-scan | New independent single-pass |
|---|---:|---:|
| Full-source validations | 3 | 1 |
| Original sealed segments | 285 | 285 |
| Archived pages | 8 | 8 |
| Quote receipts | 137,642 | 137,642 |
| Original source bytes | 214,194,780 | 214,194,780 |
| Independently frozen decisions | 3 | 3 |
| Related PRICE labels | 12 | 12 |
| Exact per-window frozen price equality | Yes | **All 12 exact** |
| Actual source-scan wall time | 75,131.281 ms | **23,923.607 ms** |

Actual source-scan wall time decreased by about **68.2%** on this copied source. This is a read-only forensic efficiency result on qian-qi, **not** a production latency, forward prediction, isolated benchmark or trading profitability claim. A preliminary separate one-pass result took 25,467.710 ms and remains intact; neither was overwritten.

Final immutable qian-qi receipt path:

`/home/qian-qi/work/train-v2-independent-source-20261013/evidence/frozen-original-3x4/independent-singlepass-3x4-final.json`

Byte-identical read-only mirror on Pocket4:

`~/.local/state/heptatrader/operator-evidence/train-v2-crosshost-singlepass-20261013/qian-frozen-3x4-singlepass-final.json`

**Final receipt SHA256:** `851b884559169287d91d55c0c5e30ecb4583f331936dc7b9def9f0e2db3ddfb4`.

Portable exact new source SHA256:
- independent full single-pass hash/ledger parser: `2cae517dac7dda6df3c13f848f4fea4484641381c842e6deff9d8d7136d13ae4`
- three-window original Decimal comparator: `aa0546b97db31420cfaf4ef26f4a16c4a022706924ee04b2d42d6bf9d4bed6d0`

Both independently executed qian-qi files match the byte-identical versions included in this Pocket4 GitHub branch. The unmodified original custody parser SHA remains `b220ceaa873abbbd02cb77e0b45256c3e6407b92ff7966e8f896da8d85913f93`.

## What is NOT qualified

- Three original decision opportunities, twelve correlated future-MID labels. **Never twelve independent trading opportunities or rewards.**
- Observed maximum absolute hindsight MID change remains 3.360471003615866799890673000 bps; frozen hypothetical stress cost + margin is 10.9133557 bps; **zero** price cells clear that assumed hurdle.
- No new model forecast, no original broker REQUEST/PREPARED action, no actual fills/executable slippage, no native LedgerWriter/independent controller identity, no account-return qualification or Artifact Owner CURRENT.
- No live source writer, old frozen output, original producer service, model weights, production trading profile or source-health HOLD was changed. No market order or broker request was issued.

This **cannot** authorize model promotion, automatic training, PAPER cutover or user-data cleanup. The new result is source integrity and forensic throughput only.

## Regression and integration

Run the existing complete historical, core Python, and source/build tests at this **exact** code commit, plus `test_train_v2_crosshost_singlepass.py`. Qian-qi passed 31 independent source/custody/singlepass focused cases, including ten single-pass equivalence/adversarial checks; the existing three-scan oracle and its original source remain immutable. New output files are create-only and original failures are retained. The Draft PR must remain unmerged until exact remote CI and independent owner review.
