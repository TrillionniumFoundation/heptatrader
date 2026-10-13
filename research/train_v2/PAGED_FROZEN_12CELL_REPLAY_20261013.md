# Train V2: immutable 3 x 4 price cohort survives paged source growth

**Date:** 2026-10-13. Isolated worktree, stacked on the original paged
PAPER source reader at `1e90e40` (PR #272). Read-only PRICE-only research:
NO broker API, model, original plan/label, original source collector,
systemd timer, OMS, production trading, Actor or Artifact Owner edits.

## Original evidence remains canonical

Frozen original source: three actual one-shot source decision receipts at
00:50, 02:10 and 03:30 (Pocket4 UTC+8), each with four nested and CORRELATED
5-, 15-, 30- and 60-minute price outcomes. Twelve label observations are NOT
12 independent decisions or trades.

The original immutable complete 12-cell audit at **269 naturally sealed**
numeric quote segments is bound by SHA256
`5da69b7245d5c2e29af45b3f49d2ebf56e0463e103ebb048757b55e1e5f6329a`.
The separately authored source-pinned closeout SHA256 is
`62786642876b7aaae6463f5ff728b6add9c11d65ca17934ed995f85fe5104156`.
The frozen 269-segment source fingerprint is
`cd8540b689d4ae75d76ec34fe0e6f72a06a0df729b8dd8d4a12cd13f5a5658db`.

The independent source-closeout audit IDs are not literally identical because
they were made at different wall times: the closeout verification explicitly
permits that audit-id difference, but requires complete equality of every
other economic/authority/denominator field. The original files and both prior
receipt hashes are preserved unchanged. All 12 original outcome files are
opened, original input/timer identities are rechecked, and each price result
is RECOMPUTED from locally verified original sealed source.

## What the new paged continuation proves

The previous full-archive reader has a deliberate 320-segment/256MiB
budget and must not silently drop early source events when source grows.
The opt-in paged reader validates ALL original archive pages/segments but
retains only a bounded 62-minute price window around each of THREE
precommitted source decisions. It never rewrites the old window, source,
quote expiry or model score.

The three independent paged scans must produce exactly ONE identical
SEALED numeric source frontier: same full sealed SHA fingerprint, sealed
segment/quote counts, page count/hash inventory and frozen-original
269-segment prefix SHA. Archive rotation between scans fails closed.
Each of the three windows must be disjoint and bind its distinct original
source receipt; source observations cannot be duplicated to artificially
inflate the cohort. The existing canonical original
`price_only_cohort_audit.audit` and `verify_final_price_audit` are reused
on the combined bounded quote index, not replaced by a custom more
permissive economic evaluator.

### Real failed checks retained, and corrected frontier definition

Two initial attempts did NOT pass and were not overwritten:
`rejected-first-original-claim.log` records a mismatch between two
separately timed audit IDs, not a changed financial outcome; the original
date-specific identities must remain different.
`rejected-active-size-mismatch.log` records refusal when the number of
bytes in the **currently active, unsealed** source file changed during
three consecutive scans. The active file is NOT eligible outcome input
and legitimately appends every second. The correct cross-scan fence
binds every SEALED original fingerprint, archived page and frozen quote
identity, but does not demand that the unsealed file size stop growing.
Any sealed original movement still rejects.

### Actual Pocket4 result

At the independently verified final scan the original source had
**286 sealed numeric segments**, 17 legitimately appended since the
269-segment frozen original price audit. The old first-269 prefix matched
byte-identically under its pinned hash. ALL original 3/3 decision identities
and 12/12 observed price labels reproduced exactly, including the
previously sealed outcome JSON SHA of each. The isolated new final
receipt also binds SHA256 of the exact review implementation and its
original paged/source/outcome dependencies.

Initial successful receipt, SHA256:
`2db66c40994ea545512bb3167b6f84212dcce8f88abf015e26eb08246d5a1236`.

Final source-code-inventory receipt, SHA256:
`aa78896810957befa6e005e8df746cf25114c4aeb8460f77942c2a6066bdd29b`.

Private evidence files:
`~/.local/state/heptatrader/operator-evidence/train-v2-paged-12cell-20261013/`

The original cohort's largest absolute hindsight MID movement was
**3.360471003615866799890673000 bps**; the frozen hypothetical
spread/fees/slippage plus margin threshold remains **10.9133557 bps**.
Zero of twelve cells clears that assumed threshold. These values are
post-hoc price observations, NOT verified executable bid/ask fills,
realized net financial rewards, independent trading alpha or a candidate
promotion. No new prediction, current Source/REQUEST/PREPARED event or
model evaluation was generated.

## Regression and remaining prerequisites

Adversarial tests cover sealed source rotation, old prefix change,
archive-page hash drift, sealed quote-count change, active-file append
(with unchanged sealed frontier), missing third window, duplicate quotes,
overlapping windows, mutated original receipt and false effect authority.
The underlying paged source tests independently cover 321 segments
and original archive-page boundary checks.

Future source growth can be reviewed only with a NEW create-only receipt,
never by overwriting either previous successful or failed evidence.

Production acceptance remains HOLD for native authorized Generator,
independently controlled Observer, real original REQUEST/PREPARED model
prediction, true behavior propensity, executable cost and broker order/
account reconciliation, independent new prospective economic performance,
and Canonical Artifact Owner CURRENT. Do not clean up evidence or deploy
a research-only price audit as a trading strategy.


## Exact final regression and resource boundary

- New frozen-cohort paging test module: 16/16 passing.
- Historical research suite: 539 executed, 7 pre-existing skips.
- Core Python suite: 2,643 executed, 23 pre-existing skips.
- Source/build/ownership lane: 106 passing.
- Original archived input: 286 sealed numeric segments at final scan,
  8 hash-linked archive pages, and 4,532 selected quote records across
  the three bounded windows.
- Final receipt SHA256: aa78896810957befa6e005e8df746cf25114c4aeb8460f77942c2a6066bdd29b.
- Preserved both failed machine transcripts and the earlier successful
  pre-source-inventory receipt as separate immutable files.
- A new original quote/snapshot after this timestamp requires fresh
  verification; a completed read-only check is not a running monitoring
  service or a durable independent learning owner.
