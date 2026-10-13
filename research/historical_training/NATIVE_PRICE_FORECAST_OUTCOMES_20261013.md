# Train V2: two genuine Native/Numeric forecasts and original PAPER outcomes

2026-10-13, pocket4. Research-only, isolated branch from frozen Native
price-forward source commit 3756497. No existing PAPER trader, Broker
Execution, OMS, Laya heavy-model source admission, old Trial3, any
model weights, original forecast plan/input/prediction/commit or
original quote writer was modified.

## What was genuinely observed

Two distinct original PAPER quote decisions occurred in newly preregistered
prospective Numeric+Native shadow capture slots at approximately 12:27 and
12:30 (Pocket4 UTC+8). Each actual original frozen generation-42 model
predicted a 300s future EUR/USD midpoint move, and both predictions were
fsynced BEFORE their original fixed deadlines and horizon expiry.
They are model forecasts, not a Laya7 GPU prediction, BUY/SELL strategy
decision, original broker order or signed Canonical Agentd LedgerWriter.

The exact original models predicted:
- Slot 01: +0.6625708695529248 bps
- Slot 02: +0.6690629418227435 bps

After natural original quote-source segment closure, the independent
read-only outcome evaluator rescanned the original sealed chain, rederived
the exact quote identity and MID reference price, and assessed the
PREDECLARED 300,000ms horizon from original forecast anchor_ms
(not an opportunistic new horizon or a later source replay cutoff).

Observed original MID changes:
- Slot 01: -0.0895086868180556923049380000 bps
- Slot 02: -0.2685272622302979309974450000 bps

The forecasts were BOTH wrong in direction. Their source decisions differ,
but the two 300-second horizons overlap and are correlated. There are ZERO
statistically independent trading reward samples, ZERO actual broker fills,
and ZERO evidence of positive realized or expected after-cost PnL. The
original frozen hypothetical stress cost plus safety margin is 10.9133557
bps. This is a diagnostic comparison, not executable slippage or strategy
economics. The correct actual trading action remains NO_TRADE.

## Critical exact-replay blocker retained

The original laya_native_price_forward.verify independently reconstructs
the original as-of quote/source, Native input, frozen weights and model
result. It requires exact equality of floating-point f64 values. On a
subsequent current runtime replay, it raised
NATIVE_FUTURE_MODEL_OUTPUT_MISMATCH.

Independent isolated recalculation of exact original sample/native binary
found current replay values:
- Slot 01: 0.6625708695529247 vs frozen 0.6625708695529248,
  exactly 1 ULP at the original frozen magnitude.
- Slot 02: 0.6690629418227438 vs frozen 0.6690629418227435,
  exactly 3 ULP at the original frozen magnitude.

This remains an ORIGINAL STRICT VERIFIER FAILURE. The original source,
model, signed plan and forecast bytes remain unchanged, and no old plan
is upgraded by tolerance. Multiple repeated CPU scores gave identical
new values; varying OpenBLAS thread count did not recover the original
bitwise frozen floats. An engineering-only independent diagnostic may
inspect the complete prior verifier flow, independently recalculate the
original Numeric score/source SHA and report bounded <=4 ULP differences.
It does NOT mark native_model_output_recomputed as an exact product pass
and does not authorize Canonical Owner or promotion. A >4 ULP mismatch
fails closed; this is an exploratory post-hoc bound, NOT an accepted new
a priori statistical test threshold.

Immutable supplemental original-price/replay-HOLD receipt:

~/.local/state/heptatrader/operator-evidence/train-v2-native-price-forward-20261013/independent-two-native-price-outcomes-replay-hold.json

SHA256:
bbacabe8aa6f417c1785c66ed05d10ce00aad65fae9de3b9dacd8ed8f0163384

Its state is PRICE_OUTCOMES_OBSERVED_NATIVE_EXACT_REPLAY_HOLD.
The original failed traceback and all old source bytes are retained.

## New deterministic future-only score codec

native_score_ticks.py introduces a SEPARATE predeclared prospective
numeric score profile: 1e-8 bps ticks, decimal-str f64 then
ROUND_HALF_EVEN, source model/native binary pinned, deterministic
content-addressed output. It rejects unknown input types, non-finite
values, extreme magnitudes, authorization changes, mismatched frozen
model IDs and results within 8 f64 ULP of a rounding-half-tick boundary.
That guard prevents silently ambiguous adjacent tick choices near the
edge of precision. The two previously observed 1/3-ULP variations map
to the same prospective representation, but it would ONLY be permitted
in a NEW preregistered future experiment with this exact codec version.
The original two f64 observations are NOT rewritten or requalified.

The future codec does not solve true behavior propensity, original
REQUEST/PREPARED binding, host-root identity, independent durable
LedgerWriter/Observer ACK, DatasetSnapshotReceiptV3, original fills,
commissions/slippage, statistical advantage, or CURRENT Artifact Owner.

## Genuine admission state

Original Native FULL CARGO WORKSPACE was qualified separately earlier.
This work does not repeat or alter original Rust products. It is a
real post-prediction original-price research audit, with a deliberately
unresolved original bitwise re-verification gate.

One execution per forecast and no new training/actor/trader authority.
An original PRICE target is NOT broker net PnL. The 3x4 earlier
price-only cohort and its 12 hindsight returns remain a separate
non-independent, no-model population.

To reproduce focused tests, use the historical training Python suite
with the existing pinned source and read-only original files.


## Final independent source-pinned receipt

The final variant binds SHA256 of the new original-price evaluator, future
score codec and original frozen model verifier, and reports the exact
sealed original PAPER source fingerprint. It retains the exact 1/3 ULP
strict-verifier failures and 2/2 observed wrong-sign price outcomes.

Immutable file:
~/.local/state/heptatrader/operator-evidence/train-v2-native-price-forward-20261013/independent-two-native-price-outcomes-sourcebound-final.json

SHA256:
08221786c16beac2ff37faba746757cc573d2d9c1872efae225fee92e58da121

The original missing-native-exact-replay failure remains a product HOLD,
even when the price-only outcome observer uses independent bounded
arithmetic to display the already matured price movements.

A future-only quantized score codec was unit-tested but NOT used to
retroactively repair or reinterpret either of the two frozen f64 plans.
It must become part of an independently preregistered future profile,
with truly qualified new prospective time-series and model evidence,
before any new acceptance or trading authorization can be considered.


## Final exact pocket4 regression and deployment status

- New post-prediction source/ULP/price adversarial tests: 15 passed.
- New **future-only** fixed-scale score serialization tests: 10 passed.
- Full historical-training source suite in the pinned Laya runtime venv:
  488 passed, zero skipped.
- Full core Python suite in system Python: 2,546 passed, 23 pre-existing
  skips. Original separate production/process tests were not replaced.
- Source/documentation/build ownership lane: 106 passed, no skips.

The exact source-bound supplemental receipt was created once after the
final new Python source hash was pinned, without modifying original
prediction files. The existing PAPER Execution, broker order channel,
Laya source guard and all old Trial3/future experiments were not
restarted or reconfigured. The native exact f64 replay gate remains HOLD.
All price diagnostics and hypothetical stress comparisons are research-
only and no product, model promotion or trading permission is awarded.
