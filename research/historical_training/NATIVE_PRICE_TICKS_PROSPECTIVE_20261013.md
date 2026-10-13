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
