# Native fixed-Tick prospective pair: independent sealed-price closeout

2026-10-13, pocket4. Isolated research branch stacked on fixed Tick
prospective forecasts at exact commit 351fcd5. Existing two plans,
source, model, Native binary, prediction, commit and strict-f64-failed
legacy experiments are immutable and never updated by this reviewer.

## What is being observed

Two distinct, actually preregistered 300-second PAPER mid-price
forecasts were sealed independently. The horizons are non-overlapping
with a fixed 45-second gap; they are NOT enough to prove statistical
independence, model profitability, calibration, financial reward or
canonical Product Owner eligibility.

- Window 01: original prediction +0.73047167 bps, score_id bound in
  original prediction.json, original input 13:14:52.542 (+08).
- Window 02: original prediction +0.73336015 bps, different score_id
  and original source, approximately 13:20 local (+08).
- Both frozen predicted price moves were below the old hypothetical
  10.9133557 bps cost-plus-margin benchmark. No order was issued.

The separate native_price_ticks_outcome.py already checks each frozen
plan/input/prediction/commit, Native model replay, exact 300-second
maturity, original source quote receipt, source price endpoint and
strict 5-second grace. Until the original containing quote segment
naturally seals, the result MUST remain
WAIT_ORIGINAL_SEALED_INPUT_COVERAGE. It must not use mutable current
source, stop the writer, synthesize missing endpoints, convert WAIT to
zero return, or retry/rewrite the original prediction.

## Additional cohort review

native_price_ticks_cohort_closeout.py calls the existing independent
frozen two-forecast source reviewer BEFORE either outcome scan and
then runs both original sealed-outcome checks. It binds each outcome
to its own distinct frozen plan, exact source receipt, model, score,
300-second due, fsynced prediction timestamp and original source
fingerprint.

Critically, both observed original outcomes must refer to the SAME
re-verified sealed archive snapshot. A concurrent natural archive
rotation between the two scans fails closed as
TICKS_CLOSEOUT_SOURCE_FRONTIER_CHANGED_DURING_READ; it cannot silently
compare outcomes from different source frontiers. Subsequent retry
uses a NEW output path, with the original receipts unchanged.

States are explicit:
- WAIT_NATURAL_ORIGINAL_SOURCE_SEAL for unavailable naturally sealed
  source input or endpoint.
- CENSORED_PRICE_COHORT_NO_EFFICACY_INFERENCE if final original price
  endpoint is missing, gapped or otherwise censored.
- TWO_SEALED_PRICE_OUTCOMES_ONLY_NO_ALPHA only if both original
  price labels are sealed, independently reconstructed and exact.
  Even then this means TWO PRICE observations and ZERO ACTUAL TRADES.

The new cohort aggregation methodology is post-hoc diagnostics, NOT
an independently preregistered economic/clinical-statistical
effectiveness protocol. It reports direction count only, without any
p-value or alpha claim. The old strict-f64 failures remain failed.
No inferred fills, slippage, actual net PnL, synthetic behavior
propensities, new Candidate/Owner approval or trade permission exist.

## Operational boundaries

The new evaluator is read-only, never imports IB trading APIs or
a real model inference/trainer, and cannot change the original
source collector. Two actual original forecasts remain distinct,
no new predictions are manufactured. Its only write is a bounded,
private, create-only, fsynced JSON review receipt. No production
service, timer, model weights, GPU permission, broker order or
original signed trial plan is changed.

Acceptance requires dedicated adversarial tests and the complete
historical, core and source partitions. A successful sealed-price
label may disprove direction accuracy, but cannot establish executable
net financial reward or change original model promotion HOLD.

## Actual natural-seal price outcomes, October 13

The original 13:07–13:37 (UTC+8) hash-chained PAPER quote source segment
naturally ended with its original END record at approximately 13:37.
The reviewer neither restarted nor forced the feed collector and did not
substitute an unsealed partial prefix. All 282 naturally ended source
segments produced the SAME independent archive fingerprint on both
result reads:

bf5847eddedf53895f3d3a6b8701f1df020fa452a96855404591110ce034803e

The original two previously sealed models and plans were independently
replayed and both 300-second endpoints became qualified PRICE-only
observations from naturally ended PAPER source data:

| Original slot | Frozen model prediction (bps) | Original future MID change (bps) | Direction |
| 01 | +0.73047167 | +0.626541179419017314913020000 | correct |
| 02 | +0.73336015 | -0.1789957443761774563809750000 | WRONG |

Real original-price direction accuracy in these **two** separate
predictions is 1/2. There are ZERO observed actual strategy executions
attributed to these forecasts, and ZERO economic net-PnL trade samples.
Both original price changes fall below the PRIOR HYPOTHETICAL
10.9133557 bps cost-plus-margin threshold; this is not a measured
fill/transaction-cost qualification. The original f64 strict replay
failures stay FAILED, not requalified by the newer fixed-tick codec.
Model trading/promotion remains HOLD.

Before natural source closure, the same independent reviewer correctly
returned WAIT_NATURAL_ORIGINAL_SOURCE_SEAL with 0 observed outcomes;
that immutable pre-seal receipt was not changed.
Earlier WAIT SHA256:
bd3deb630301f89b7576f11feb45459e7b6d1c91648eb256fe58fcc3155418dc

Final independent natural-seal / cost-flag-checked receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-native-ticks-prospective-20261013/real-ticks-pair-closeout-final.json

Final SHA256:
86cdc14215289cb9fd4931743ba36dcb0bba16723f2708e75a387c418bf94794

The new cohort aggregation was written after the originally frozen
individual forecast registrations; it must NOT be described as an
independently preregistered statistical efficacy protocol or an
independent canonical Observer signature. These are real price
observations only, with no new broker query or order.

## Capacity risk exposed during source replay

The original source-index loader currently has MAX_SEGMENTS=320 and
MAX_SOURCE_BYTES=256MiB; the actual original feed already spans 282
naturally ended segments plus the active newest segment. Future
replays will fail closed at a bound unless separately reviewed
pagination/append-only source indexing is implemented. Do not bypass
these checks by silently dropping old segments or raising the cap and
calling that a source-integrity fix.

## Exact final tests (same code tree)

The final independent reviewer includes original hypothetical cost-flag
cross-checks in addition to frozen forecast ID/time/source matching.
Reversing the order of the two original review paths does not change the
original per-plan observed pairing.

- Dedicated original natural-seal cohort tests: **18 passing**.
- Full historical research tests with the pinned Pocket4 Laya runtime
  Python: **539 passing, no skips**.
- Full Core Python suite: **2,597 passing, 23 previously skipped**.
- Source/build/ownership suite: **106 passing, no skips**.
- Test transcripts and reversible isolated source tree:
  ~/.local/state/heptatrader/operator-evidence/train-v2-native-price-pair-closeout-20261013/
  and /home/alex/work/heptatrader-train-v2-native-ticks-price-closeout-20261013/

No original forecast, source archive, broker, active learner, native
Owner, fixed production release path or older failed f64 evidence
was mutated or reclassified.
