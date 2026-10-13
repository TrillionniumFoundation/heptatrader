# Train V2 — New prospective Native/Numeric fixed-score shadow experiment

13 October 2026. Pocket4 / research-only. Independent new branch descended
from the actual original price-outcome branch f65dbe7. No modification
to previous 12:27/12:30 f64 forecast plans, their signed/frozen bytes,
original source archives, live PAPER Execution, OMS, Laya model/weights,
Laya Guard, existing timers or canonical Artifact Owner.

## Why a NEW candidate profile is necessary

The two actually precommitted future price forecasts had 1-ULP and 3-ULP
raw f64 differences on later identical frozen model/source replay. The
original exact-f64 verifier failed and remains FAIL. A post-hoc ULP
tolerance cannot qualify those old immutable plans. Both earlier
price forecasts were directionally wrong and below historical assumed
after-cost stress; neither is an executable trade or alpha claim.

The distinct future-only numeric_score_ticks V1 codec already existed
and was independently unit tested, but no actual prospective canary
was precommitted with that codec before model inference. This branch
implements a NEW future-only experiment with its own immutable
plan, capture and verification. The legacy strict-f64 verifier is
untouched and explicitly rejects the new profile.

## New frozen timing and original evidence

The new schema heptatrader.native-price-ticks-future-plan.v1:
- requires fresh explicit registration 60-600 seconds before an original
  16-second capture slot;
- pins the frozen generation-42 original numeric readout, actual native
  binary SHA256, complete source module inventory and exact
  original no-historical-mix PAPER quote profile;
- receives no training-actor, trading, product owner or broker-write grant;
- records one exact original source receipt and feature snapshot before
  model scoring, fsyncs input.json, prediction.json and commit.json
  before the original fixed window closes;
- writes a separate frozen score_id with 1e-8 bps tick
  HALF_EVEN decimal-str f64 conversion, and refuses near-half-tick
  ambiguity within 8 f64 ULP; raw f64 is diagnostic only;
- independently rechecks the exact original hash-chained PAPER
  source prefix, as-of sample, actual frozen model/binary and
  canonical tick score on later replay;
- fails missing/late source or unknown partial crashes rather than
  fabricating an input or silently reissuing a failed slot.

The deterministic tick score is a new, publicly described
protocol-level rounding rule. It does NOT retroactively replace
the old f64 plan bytes or prove bitwise repeatability of the
underlying floating point model. A new-model score matching
to 1e-8 bps is a bounded *engineering* reproducibility gate,
not evidence of scientific directional performance.

## Mandatory negative evidence

Dedicated tests simulate 1- and 3-ULP replay differences,
larger score drift, plan/source/model/binary changes, rehashed
trade-grant plan modifications, tampering with immutable predictions,
non-finite scoring, missing original source, duplicate capture,
premature trigger, and legacy profile incompatibility. No broker
API, real model weight or trading service is used in synthetic tests.

Any actual future live forecast must be generated from a genuinely
new source anchor AFTER its preregistration time. The original 5-minute
price outcome must come independently from later naturally sealed
original segments; an unsealed quote tail cannot be promoted by
a hindsight read or synthetic estimate. An observed price label is
still not broker executed net PnL. No new model or strategy should
trade until genuine separate original REQUEST/PREPARED evidence,
native LedgerWriter/Owner trust, full costs and statistically
credible independent after-cost alpha are established.

## Current product boundary

Independent prospective sample count, prediction accuracy and
economic qualification can be assessed ONLY after fresh captures
and mature original source proof exist. Existing 2/2 old
strict-f64 failures remain frozen. This is NOT a Laya7 GPU model,
not a BUY/SELL agent, not actual trading, not a canonical owner
decision or a native ledger write. If a new capture is missed,
the missed state remains a failed source/forward sample.


## Exact initial source-tree and synthetic acceptance

On pocket4 2026-10-13, after freezing the NEW future tick parser
and all current source modules, the initial full regressions passed:

- New prospective tick-shadow focused tests: 15 passed, no skips.
- Full historical research: 503 passed, no skips in the isolated pinned
  Laya runtime Python venv.
- Core Python: 2,561 passed, 23 existing skipped.
- Source/build/ownership: 106 passed, no skips.
- All test evidence is private under:
  ~/.local/state/heptatrader/operator-evidence/train-v2-native-ticks-prospective-20261013/

These are SOFTWARE acceptance tests. The new model output has NOT yet
been proven on a new actual post-registration original PAPER quote.
The prior two legacy strict-f64 replay failures and 2/2 incorrect
price directions remain immutable. No actual signed Product Owner,
broker execution, new trade order or future economic result has been claimed.


## Actual new ORIGINAL PAPER Native/tick model forecasts (October 13)

The original new-schema source tree was committed as 81cba97 before
these two original market windows began. Both plans were registered
using Pocket4's actual system clock and retained as immutable plan.json.

Actual first source decision:
- Registered at 13:12:50 local UTC+8, first slot 13:14:50–13:15:06.
- Original quote at 13:14:52.542, actually available 13:14:52.847.
- Model/feature decision 13:14:54.565, committed/fsynced 13:14:54.571.
- 300-second price forecast +0.73047167 bps (integer 73,047,167).
- Independent original as-of source + frozen Native/Numeric model
  recomputation returned the exact SAME prospective score_id.
- No original legacy f64 raw-bit comparison has been claimed.

Actual second source decision:
- Preregistered with 163,971ms lead; second model window starts exactly
  45 seconds AFTER the first original 300-second horizon deadline.
- 300-second model price forecast +0.73336015 bps (ticks 73,336,015).
- Independent ORIGINAL source/Native/Numeric replay returned the exact
  SAME frozen second score_id, different from first.
- Two price horizons do not overlap; original price-only model
  observations remain far too few for statistical/financial qualification.

The protected two-window, independently reread source/Native score review:
~/.local/state/heptatrader/operator-evidence/train-v2-native-ticks-prospective-20261013/two-new-native-ticks-disjoint-forecast-evidence.json

Immutable receipt SHA256:
ce6365c39737f2926ca16203073f455d0ecac1b5e93da8f80c8e01c7adbdc17d

Both predicted absolute price moves are <10.9133557 bps of the PRIOR
FROZEN hypothetical stress cost plus safety margin. This is a
pretrade research ABSTAIN diagnostic, not broker-executable PnL.
Trading rewards, real fills, slippage qualification, Canonical
Owner selection and model promotion are all ZERO / HOLD.

## Correct price-only outcome procedure

An additional separate native_price_ticks_outcome.py independent
read-only evaluator verifies the COMPLETE new tick proof BEFORE reading
any outcome source. It requires the naturally sealed original PAPER
quote/index and fixed previously declared 300-second endpoint with
5-second grace, exact original price identity and reference MID,
source path continuity and no fabricated points.

Before the new original input segment is sealed it emits
WAIT_ORIGINAL_SEALED_INPUT_COVERAGE. When the input is sealed but the
endpoint isn't, it emits WAIT_ORIGINAL_SEALED_PRICE_COVERAGE.
Missing/late endpoints remain explicit censored states; neither is
ZERO RETURN, NO_TRADE NET PNL, nor an eligible counterfactual trade.
An observed price movement is a price-only label, not actual
broker realized PnL.

Initial true first-source read-only output (prior to natural sealing):
~/.local/state/heptatrader/operator-evidence/train-v2-native-ticks-prospective-20261013/real-ticks-01-price-preseal.json

SHA256:
003eefddb62fcfd18f577397fd8098f93d1072ddecae456213cb0649b31f4e15

State: WAIT_ORIGINAL_SEALED_INPUT_COVERAGE. The original file,
source signature identity and frozen model prediction remain unchanged.
No original quote writer was forced to rotate and no history was
reclassified to create a premature result.


## Two-source final cohort and pre-seal result receipts

The independent two-window reviewer reread BOTH real sealed prediction
triples and the pinned original source/frozen generation-42 Native model
using the new fixed tick profile. The two quote receipts, source
timestamps, exact binary/model SHA256 identities, original plan IDs,
source snapshots and score IDs were all distinct and verified. The
300-second price horizons are physically nonoverlapping by a 45-second
first-due-to-second-slot buffer; this is not a statistical independence
or executable-profit claim.

Forecasts (not order returns):
- First: +0.73047167 bps, 73,047,167 quantized score ticks.
- Second: +0.73336015 bps, 73,336,015 quantized score ticks.

The original-stressed hypothetical cost plus safety margin remains
10.9133557 bps, so both forecast magnitudes are below the old
research threshold. Even a hypothetical positive price move alone
would not authorize an actual trade.

Final immutable two-window forecast audit:
~/.local/state/heptatrader/operator-evidence/train-v2-native-ticks-prospective-20261013/two-new-native-ticks-disjoint-forecast-evidence.json

SHA256 ce6365c39737f2926ca16203073f455d0ecac1b5e93da8f80c8e01c7adbdc17d

Independent true-source read-only outcome checks, run after the
horizon due but BEFORE the input segment naturally sealed:
- Slot 01: WAIT_ORIGINAL_SEALED_INPUT_COVERAGE,
  SHA256 003eefddb62fcfd18f577397fd8098f93d1072ddecae456213cb0649b31f4e15.
- Slot 02: WAIT_ORIGINAL_SEALED_INPUT_COVERAGE,
  SHA256 7b5146d68ad11ed4d9153608007ba43b3b5f52e1d5f19cec12113ae3169e0a28.

Neither original result is represented as a filled trade, observed
direction, independent reward or zero PnL. Naturally closing source
segments may permit a later separate independent result observation.
Do not force segment rotation, shorten grace, or reissue any immutable
prior output to manufacture a result.

## Final second-stage regression

After implementing explicit WAIT for unsealed original INPUT as well
as missing original endpoint, adding the independent dual-forecast
source/horizon audit, and leaving the registered forecaster code
unchanged, the final exact worktree passed:

- Historical original research suite: 521 passing, no skips (pinned
  Pocket4 Laya runtime Python).
- Core Python suite: 2,579 passing, 23 existing skipped.
- Source/build/ownership suite: 106 passing, no skips.
- Focused independent native tick outcome tests: 10 passing.
- Focused dual-source independent decision tests: 8 passing.
- No modification to source quote writer, active PAPER Execution/OMS,
  Laya Guard/training weights, legacy f64 plans or trading permission.

The second-stage evaluator is intentionally not in the first-stage
frozen predictor FILES inventory. Its price-only outcome methodology
follows the preexisting original 300,000ms horizon plus 5,000ms
endpoint-grace rule, but it is NOT a product-owner-signed preregistered
statistical effectiveness protocol. No release qualification follows
from these software acceptance tests.
