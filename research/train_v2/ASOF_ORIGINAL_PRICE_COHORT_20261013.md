# Train V2 — as-of original price cohort hardening (2026-10-13)

This research-only change is stacked on the frozen 3x4 price-cohort audit at
9d532928a79ce896ff0ec45815cc996cde423dbe (Draft PR #244).
Do NOT deploy a new trade model or alter the PAPER Strategy, OMS, Execution,
Laya, original quote writer, scheduled 03:30 slot, old Trial3 or any original
source receipt.

## Verified defect and surgical remedy

The prior observer independently recomputed a persisted ORIGINAL price
label and checked its immutable hash and maturity, but did NOT require
persisted observed_at_ms <= the requesting audit's as_of_ms. In a
retrospective as-of audit, a hash-recomputed future-timestamped local
outcome could consequently be displayed as already observed, provided
an archive with otherwise legitimate sealed price rows was available.

The new read-only cohort auditor rejects any future-dated existing
price receipt, even if its local hash was recomputed, with
COHORT_AUDIT_OUTCOME_OBSERVED_IN_FUTURE.
The original one-shot reconciler rejects the same in both reuse and
post-observer verification paths with
OUTCOME_RECONCILE_RECEIPT_FROM_FUTURE.
The preexisting no-interpolation, maturity, source-seal, immutable result
and negative economic-authority checks are unchanged.

Two new adversarial tests modify only private TEST fixtures, rehash
original result IDs, and demand explicit refusal in each admission
path. This enforces time-as-of correctness for each persisted label;
it is NOT an independent clock or externally signed broker attestation.

## Actual original finite cohort, 13 Oct UTC+8

The frozen scheduled PRICE-ONLY source cohort is
0b0f40d0013199eb43fdb97886744b80e29f1479955c695f7d239d07bebf1ece,
12 originally declared observation cells, three slots x four horizons.
Nothing in the campaign, timer roster, original quote input, existing 5m,
15m, 30m results or cost hurdle was modified.

The 00:50 slot's original 60-minute endpoint became eligible only
AFTER the original NUMERIC writer naturally sealed sufficient source.
Its read-only 02:11 preflight was READY; the original strict observer
then made exactly one immutable 60-minute price-only outcome.
First three horizons were already independently sealed. Resulting
slot 0 hindsight original mid-price movements:

- 5 min +0.268878641625... basis points.
- 15 min +1.702898063625... basis points.
- 30 min +3.092104378688... basis points.
- 60 min +0.537757283250205019964240000 basis points.

All observed absolute moves are below the previously frozen hypothetical
cost plus margin hurdle of 10.9133557 bps, but NO original model forecast
or executable round-trip exists. This cannot prove predictive accuracy
or expected/net trading performance. The no-trade comparator remains 0.

Original 60-minute outcome receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-fixedslot-20261013/timed-campaign-v1/slot-00-h60.outcome.json

SHA256: fdf8c783f15bdf0710b8912cf08c0fdad9d99992beb6123ed51b679c379ed3ac

## Second original source/timer acceptance

The fixed 02:10:05 source timer actually executed normally.
A separate original quote became locally available at 02:10:03.364;
the input was sealed at 02:10:05.083, i.e. 1,719 ms after availability
and 2,530 ms before its original IB expiry of 02:10:07.613.
All four original future horizon labels were PENDING, the model was not
run and no order was sent.

Original slot 1 input file SHA256:
6a0002312db4d97bc8a402d00c46985868b56463abdcc3879d1ceb0f9ed772

Independent already-appended source prefix recheck used exactly 72,322
original bytes and reproduced frozen feature/chain identities. It does
NOT independently sign the product's Generator, Observer or Agentd Owner.
Replay report SHA256:
8f21294d00ff7c204614e02f605d15203bfda279a689d29ed944631971485e14

## Independent new full 12-cell as-of audit

The create-once report at approximately 02:12 (+08) established:
- 4 VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY, slot 0 5/15/30/60 minutes.
- 4 PENDING_NOT_MATURE, slot 1.
- 4 NO_SOURCE_INPUT_NO_PRICE_OUTCOME, future slot 2 (03:30).
- 0 model forecasts, 0 signed independent model observers, 0 IB orders,
  0 after-cost qualified strategy outcomes, automatic selection disabled.

Read-only report:
~/.local/state/heptatrader/operator-evidence/train-v2-cohort-asof-20261013/slot0-four-slot1-signed-source-20261013.json

SHA256: ca9bd985d54328ebf5a63a6a1e8f00dcd329717227d634bae420f4ae6d28a53f

An empty future slot does not imply a missing market quote or zero returns.
The previously failed unrelated manual 00:20 slot remains missed; it is
never retroactively merged with this separate successful scheduled campaign.

## Outstanding gate

Allow each slot 1 or 2 price outcome ONLY after its exact maturity and
original source segment seal. Preserve every failed/censored observation,
not just successful labels. No original LLM REQUEST/PREPARED or true behavior
propensity, product-rooted Agentd LedgerWriter, actual broker execution
cost/slippage, complete account return or signed Artifact Owner CURRENT
is derived from this PRICE-ONLY campaign. Never promote a model here.

The normal historical/core/source tests and exact-commit CI must be
independently checked prior to merging this Draft child PR.

## Final exact-code-tree validation and subsequent honest WAIT

- Added two hostile future-dated receipt tests: cohort-audit 15 passed,
  original outcome reconciliation 22 passed. No skipped focused tests.
- Historical research: 400 passed, 7 pre-existing skipped.
- Core Python suite: 2,438 passed, 23 pre-existing skipped.
- Source/build/documentation lane: 106 passed.
- Second scheduled quote source prefix independently reproduced in 72,322
  source bytes. Slot 01 is still a model-free NO_TRADE price observation.
- At 02:16 (+08) slot 01's five-minute price matured but original input
  segment remained unsealed: WAIT_INPUT_ORIGINAL_SEGMENT_SEAL. No label was
  written. Slot 01's 15/30/60m remain future, slot 02 is not yet due.
- Immutable 02:17 additional 12-cell audit SHA256:
  25ebe65b3dbbdcd4fee51c3749623f999f5b8cd3b66d198b336c462930b093f6
  Stored as slot1-five-minute-mature-unsealed.json under the same evidence
  directory, without rewriting the earlier 02:12 snapshot.

No prior immutable price outcome, source prefix, or timer was rewritten.
Execution / PnL and real training systems remain outside this branch.
