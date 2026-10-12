# Train V2 — finite original-price outcome reconciliation (2026-10-13)

2026-10-13, Pocket4. Research-only child of the fixedslot campaign source
11739e01c29d267fd5875ceddae866047d15bd1c (Draft PR #239).
The active trading strategy, PAPER broker profile, Execution, Laya Trainer,
old Trial3, original market source and the 02:10/03:30 one-shot timers
were not modified or restarted.

## Actual next barrier

The first pre-registered original PAPER quote slot at 2026-10-13
00:50 Pocket4 UTC+8 was genuinely captured and locally reverified 218ms
after original quote availability. A 00:57 on-demand five-minute outcome
attempt returned WAIT_INPUT_ORIGINAL_SEGMENT_SEAL, correctly refusing
the then-active append-only NUMERIC source segment.

The original market-data writer rotated/sealed its prior segment before
the next read. At 01:08 the 5-minute label could be computed from the
strict *sealed original source*, not from revised IB bars, guessed
future prices or a file that was still being appended. At 01:13 the
same was true of the 15-minute original outcome.

## New code

sealed_outcome_reconcile.py safely invokes the *existing* original
prospective_horizon_observe after requiring a matching presealed campaign,
local pre-registration, exact immutable slot input, correct one-shot
timer roster/result, original source and original outcome maturity.

Each invocation is finite; no always-on observer, model, broker API
or order is installed. Default mode is read-only: it can report
WAIT_ORIGINAL_MATURITY, WAIT_INPUT_ORIGINAL_SEGMENT_SEAL,
WAIT_END_OF_ORIGINAL_SEALED_COVERAGE, or READY_ONLY_NO_WRITE.
An optional explicit --commit-original-outcome asks the pre-existing
strict observer to create exactly one immutable original price outcome.
Missing an original input never silently becomes a negative return.
A partially missing input/timer pair or source symlink is a hard error,
not a missed/zero sample.

For an existing result, the program does NOT write a second outcome:
it verifies the original result_id, original PRICE return/censoring,
exact input and deadline, original source identity, no trader/Owner
authority, and correct original maturity. This remains valid when
later sealed original segments extend the observed source inventory.
Any rehashed modification to the previously sealed return, authority,
maturity or source causes hard failure.

The observer records only ORIGINAL PAPER MID price movement or
explicit censored source/endpoint status. It does not give credit
to a model, reconstruct missing original request/PREPARED, infer
execution costs, assert independent observer signature or prove alpha.
An append-only hash chain is local source-integrity evidence, not a
cryptographic broker signature or a hostile same-UID trust boundary.

## Real Pocket4 evidence

Campaign identity:
0b0f40d0013199eb43fdb97886744b80e29f1479955c695f7d239d07bebf1ece

One-shot roster identity:
979655223eb641c05d6acd4773a481809721e38524bd8634510181434c15976b

First original source input SHA256:
7743416970ce926096954e31e4d78b9ef966ece1bdf73c93fb9abcc287bea757

First 5-minute source-only observed outcome, created once after original
segment seal and independently verified by the new reconciliation:
c81e0d14f55f4fd3526fc6dec59854e07839c348886619a6a56cd27047d3131e

First 15-minute source-only observed outcome, created once after original
segment seal and independently verified by the new reconciliation:
bc2701a4f46e8bb54b14093de5778d8a185627e1ca4654572baf4363702c2f92

Paths:
~/.local/state/heptatrader/operator-evidence/train-v2-fixedslot-20261013/
timed-campaign-v1/slot-00-h05.outcome.json
timed-campaign-v1/slot-00-h15.outcome.json

Neither of the two labels came from a model prediction. They have no
actual fills or financial value scores. Other slots/horizons remain
FUTURE/PENDING/MISSED according to their original fixed deadlines;
they are NEVER retroactively assigned zero or marked as successful.

## Next product acceptance

1. Continue independent price result observation only after each
   endpoint matures and its original source segment is sealed.
2. Finish the pinned native LedgerWriter/Owner path with true root trust
   and original behavior-policy candidate/propensity/REQUEST identities.
3. Independently bind a real model forecast, signed before original
   PREPARED, and actual broker execution cost/outcome for a NEW trial.
4. Evaluate all predeclared samples, including censored/missed cases,
   against NO_TRADE and full after-cost risk constraints. A PRICE-only
   observed return cannot trigger trading or automatic model selection.
5. All stacked Draft PRs require separate exact commit CI and product-host
   review before integration or deployment.

Run the focused reconciliation tests and the full historical/core/source
partitions on the final exact source tree.

## Exact Pocket4 independent regression and economic caution

The final isolated code tree passed:

- New finite outcome reconciliation tests: 21 / 21.
- Full historical research tests: 400 passing, 7 pre-existing skipped.
- Full Core Python tests: 2,415 passing, 23 pre-existing skipped.
- Source/build/documentation lane: 106 passing, no skips.
- Original campaign's 00:50 source input and timer receipts unchanged;
  first price-only 5m and 15m outcome files were created exactly once.

Actual measured original midpoint moves of the first slot:
- 5 minutes: +0.268878641625102509982120000 bps;
- 15 minutes: +1.702898063625649229886760000 bps.

Both are smaller than the previously frozen original-PAPER stressed
cost-plus-margin 10.9133557 bps. These are absolute after-the-fact
PRICE observations, NOT expected directionally signed model returns,
fills, financial reward labels or proof that an executable strategy
could profit. The fixed three-slot 5/15/30/60m experiment is still
ongoing; do not cherry-pick its first two observed horizons as a
complete cohort or retroactively choose another forecast horizon.

The special reconciliation deliberately keeps complete source-index
coverage, archived original quote identity, original PREOUTCOME seal
and source-only (no model/no orders/no owner) policy. Existing output
cannot be overwritten, and its original outcome return is independently
recomputed from a later valid sealed-source archive; new appended source
segments may legitimately change the archive's global fingerprint,
not the previously sealed original endpoint's PRICE result.
