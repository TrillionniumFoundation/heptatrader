# Train V2: independent two-host custody and frozen 3×4 PRICE closeout

Date: October 13, 2026. Isolated research implementation stacked on PR #277
and its original paged reader. This is NOT production deployment, native
model inference, financial PnL, LedgerWriter, new broker calls or selection.

## Independent evidence path

The original Pocket4 frozen campaign declared exactly THREE original
PAPER source decisions (00:50, 02:10 and 03:30 UTC+8 on October 13),
each containing FOUR nested and correlated 5/15/30/60-minute PRICE
observations. That is 3 independent decisions and 12 correlated price
labels, NOT 12 independent trades. Original campaign/roster and all
3 input, 3 timer, and 12 outcome JSON files were copied read-only
to qian-qi, preserving exact SHA-256. The original complete 12-cell
audit SHA256 is
5da69b7245d5c2e29af45b3f49d2ebf56e0463e103ebb048757b55e1e5f6329a.
The original full closeout SHA256 is
62786642876b7aaae6463f5ff728b6add9c11d65ca17934ed995f85fe5104156.
The frozen first-269-segment fingerprint is
cd8540b689d4ae75d76ec34fe0e6f72a06a0df729b8dd8d4a12cd13f5a5658db.

Qian-qi already held an independently copied, sequentially transferred
snapshot containing 285 naturally ended original PAPER numeric segments,
8 chained archive pages, 214,194,780 original source bytes and 137,642
original quote receipts. The active/unsealed Pocket4 numeric segment was
NOT copied and cannot be used to fill missing outcomes. The complete copied
source was scanned separately for each of the THREE frozen ~62-minute
windows. Every segment and archive-page chain was independently verified
for all three reads, and the full current source frontier plus the first
269-segment prefix matched all three times.

The independent Python code on qian-qi uses Python standard library ONLY.
It does not import Pocket4 historical_training, train_v2, Laya source
reader or product model code. It reopens and validates every copied original
source segment, compares the full original campaign/input/timer/outcome SHA,
source quote receipt IDs, arrival vs observed time, original quote expiry,
and the complete six-quote pre-decision feature lineage. For each of the
12 price results it selects the first original event at or after the
predeclared deadline, requires <=5s terminal slack and <=30s continuous
original path gaps, and recomputes exact Decimal MID returns from the
frozen original quote. It verifies that price-only labels are not
executable bid/ask fills, actual broker reward or trade-permitted signals.

## Actual independent qian-qi result

- 285 naturally ended original segments and 8 archived pages all verified.
- All first 269 original segment SHA fingerprints preserved without change.
- Three ORIGINAL campaign decision identities reverified independently.
- All TWELVE prior original price outcomes reproduced with exact Decimal
  agreement and exact immutable original outcome JSON SHA-256.
- Max absolute hindsight MID move: 3.360471003615866799890673000 bps.
- Previously fixed HYPOTHETICAL cost plus margin: 10.9133557 bps.
- Price cells above that assumed cost threshold: ZERO of twelve.
- Actual attributed broker trades and actual net-profit samples: ZERO.
- New model forecasts, independent product owner permissions and trading
  authorization: NONE.

Qian-qi sequential actual wall time 75,131.281 milliseconds for
three complete scans of the copied 208 MiB source; this is a custody
and forensic correctness demonstration, not a production throughput
qualification, broker-signature verification or isolated benchmark.

Immutable qian-qi original report:
 /home/qian-qi/work/train-v2-independent-source-20261013/evidence/
 frozen-original-3x4/independent-full-3x4-v1.json

Read-only copied mirror on Pocket4:
 ~/.local/state/heptatrader/operator-evidence/
 train-v2-independent-crosshost-12cell-20261013/
 qian-independent-frozen-3x4-v1.json

Both SHA256:
294bba1c272a23a0f27afe3dddfa1e0c20f9a6958c47bcdac0a24e362d86e3c7

The copied raw 208 MiB source is retained on qian-qi ONLY, and is NOT
committed to GitHub or duplicated onto another disk. Original packet,
full old failed results and prior model observations remain unchanged.

## Independent portable source and failure evidence

This branch includes exact byte copies of the original qian-qi
standard-library verifier:
- research/paged_crosshost_custody.py, SHA256
  b220ceaa873abbbd02cb77e0b45256c3e6407b92ff7966e8f896da8d85913f93
- research/train_v2/independent_frozen_12cell.py, SHA256
  8fee7a813039978aaa41bd414885f1329083deb1bd0a674ed799ae288859e671.

The portable source parser includes archive-page and ledger hashes,
private-file restrictions, source-first quote receipt consistency and
a 321-segment/10-page negative regression beyond the old source cap.
Unit-level adversarial tests on independent qian-qi source: 21 passed.
A first unit test failed only because the expected error label was
PRICE_PATH_GAP while the existing rejecting validator correctly emitted
INDEPENDENT_ORIGINAL_PATH_GAP. The full original failure transcript
is preserved with SHA256
4ecef1993672ade08edac5139c5c3704f9bcdbcd72e5338e25ec68ef6ed4b317.
Only the test's error expectation was corrected, not source checks.

## Qualification boundaries

No replay retrospectively creates independent signed Producer/Observer
records, a Native model prediction, original broker REQUEST/PREPARED,
true logged action propensity, executable spread or slippage, full
account FX all-in PnL, or canonical DatasetSnapshot/Artifact Owner.
The 3x4 correlated price-only cohort did NOT beat its frozen stress
cost hurdle. No alpha, backtest promotion, automatic policy update,
trade authority or model/economic production release is supported.

Neither operator-controlled source transfer nor SHA hash chains prove
broker-signed original market authority. The business result remains
HOLD_NO_INDEPENDENT_AFTER_COST_TRADING_EVIDENCE. The growing current
source may continue appending/rotating normally; reruns require new
create-only receipts and do not replace this historical snapshot.

Run the independent tests from this repository with
PYTHONPATH=research:research/neuron_learning:scripts, and run the
original complete core, historical, and source/ownership regressions
on the precise commit before considering merge.

## Final exact isolated regression on Pocket4

After copying the exact independently executed qian-qi source, Pocket4
verified original source byte identities: independent-custody auditor SHA256
b220ceaa873abbbd02cb77e0b45256c3e6407b92ff7966e8f896da8d85913f93
and independent-3x4 wrapper SHA256
8fee7a813039978aaa41bd414885f1329083deb1bd0a674ed799ae288859e671.
Both copies match qian-qi exactly.

- Focused portable qian-qi tests: 21 passed.
- The same focused source and price tests on Pocket4: 21 passed.
- Full historical training regression: 539 passed, 7 pre-existing skips.
- Full Core Python: 2,664 passed, 23 pre-existing skips.
- Source / build ownership partition: 106 passed.
- The fixed original qian-qi receipt was mirrored byte-for-byte to Pocket4,
  both copies SHA256 294bba1c272a23a0f27afe3dddfa1e0c20f9a6958c47bcdac0a24e362d86e3c7.
- Neither host executed any new broker request, signed model forecast,
  GPU train job, trade or product-owner selection.

The source's original 285 naturally sealed segments remain on qian-qi
only. This is a **historical read-only cross-host corroboration**, not
a newly held-out independent economic performance experiment. Real alpha
and production release must remain HOLD. This PR must stay Draft until
parent PR #277, exact CI and independent owner review are satisfied.
