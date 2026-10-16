# Pocket4: bridge original PAPER quotes to research Laya price features

## Scope and limitations

This independent **research-only** bridge consumes the existing
source-archive verified, event-time original PAPER quotes. It cannot
place orders or select/trade a production model; it does not update any
Laya resident worker, canonical owner, Value plan, or original 90-slot
shadow plan.

The completed Pocket4 revised-history 14-fold GPU research selected
a small 24-32-7 MLP on validation in all 14 folds (40,767 distinct
test anchors, Brier 0.592230 vs train-prior 0.630590; NLL
1.175213 vs 1.261557). Those **revised historical IB bars** are not
the same distribution or observation protocol as original event-time
PAPER quotes. They are not time contemporaneous and do not prove
profitability, prospective price quality or 88/90 acceptance.

## Original-source readiness and causal feature construction

The existing protected read-only recent-source loader preserves sealed
archive prefix evidence. A fresh as-of snapshot on Pocket4 had
7,120 checked original quotes, including 44 strictly covered completed
five-minute bins and a longest contiguous span of 41 bins (13 required).

The price_original_live_features.py module assembles only complete
300-second buckets whose event timestamps and known-at timestamps predate
as-of. Each bucket must have at least 8 original quotes, a quote within
15 seconds of the beginning and end, and no event-time gap over
30 seconds. The close uses the last observed tick, and high/low use
only actually observed mids. Missing, crossed, partial or late inputs
are rejected, never forward-filled or interpolated.

The 24-dimensional feature vector is mathematically compared against
the existing frozen price_year_gpu.features_from_bars on synthetic
bars, without reading a future price label. The as-of source tag
remains ORIGINAL_VERIFIED_PAPER_QUOTES, distinct from IB revised
bar data.

At the first real as-of check, the 24 features were computed from 45
strictly complete original buckets; source digest and raw feature
vector were sealed. But the latest completed bar was **210.763
seconds old**, exceeding the original 60-second fixed-slot grace:
that record is NOT a valid forward prediction and must not be counted.

## Recovering and checking the last frozen historical GPU model

The selected last chronological historical fold is fold 13,
fold-13-tiny_mlp.safetensors (SHA256
7340d6429fbeb342ffd3d2038f4f3ea4b6f5252dcd6bc0efa7ef77fdd95c72e1).
The original source bar SHA and fold specification SHA were rechecked.

Only fold-13's training partition was used to reconstruct mean and
standard deviation. The original frozen model reproduced the sealed
validation **exactly** on Pocket4 CPU:

| Validation | Original GPU | Recomputed Pocket4 |
| --- | ---: | ---: |
| Brier | 0.525165915489 | 0.525165915489 |
| NLL | 1.028784275055 | 1.028784275055 |
| Top-1 hits | 3,760 | 3,760 |

Thus the stored checkpoint is reproducible against its source
and exact training normalization, without retraining or promotion.
This does NOT establish IB-to-original-quote feature distribution
equivalence: at the stale exploratory input check one standardized
feature exceeded the original ±6 clamp, with absolute magnitude
9.698 before clipping. That is a distribution-shift warning.

Read-only receipts are stored under:
~/.local/state/heptatrader/operator-evidence/laya-year-price-original-source-20261016/.

## Real future input rule

The separate single-window, research-only runner commits a fixed
five-minute bar-close anchor **before** reading the original quote
window; uses the last 13 strictly completed bars; and seals its
frozen-model and train-frequency-prior probabilities before the
next completed bar close. If the anchor is stale, the source is
incomplete, or an input changed, the slot remains failed/censored.
Future labels are not used for choosing weights, scaling, or input.

The next original quote bucket's close is the five-minute observed
outcome; the source remains distinct from IB revised historical OHLC.
One successful slot never qualifies a 90-window campaign, and no
statistical or trading promotion is performed.

The original fixed 90-window shadow plan is independent. It must not
be modified, re-scored, or counted as a Tiny MLP production screen.

## Actual first frozen original-quote future window: not qualified

The research runner precommitted its fixed anchor at
1792124400000ms, before opening the as-of original quotes.
Original input age was only 10 seconds; the original-source
prediction was sealed at 1792124410941ms, before the
five-minute event-time endpoint. Prediction SHA256:

  da437f80c1785d413df7a8a5594c617e08c01621b5c9dd1f95dbeb86541af94e

After the actual next original-quote five-minute bar closed,
the result was OBSERVED, with original event receipt
  eaebbcd28e939319a289a318ad3e3ffe25ab01e4abb399cd87d1e325bd34a27d
and actual return **+0.311569 bps** (seven-bin target 3).
The frozen Tiny MLP predicted class 2 and **missed**: Brier
**0.7769793**. The train-only class frequency prior predicted
class 3 and **hit**, Brier **0.2370791**. The Tiny MLP result
is objectively worse on this initial original-source forward
window. It remains a single observation, not 90-window power,
profitability, or a candidate admissible for trading.

The immutable plan and stage-one prediction were recorded BEFORE
outcome arrival; full result is stored on Pocket4 at:

  ~/.local/state/heptatrader/operator-evidence/laya-price-original-true-future-20261016-1/

The exact version of the source bridge and runner used for
this first prediction is separately preserved as
frozen-feature-module-v1.py and frozen-runner-v1.py, each
checked against the precommitted SHA256. This is necessary
because the later research-only historical type-compatibility
bug fix changes the bridge module checksum.

### Historical decimal-source compatibility fix

An independent deterministic comparison across 1,001 of the
69,815 archived five-minute examples initially discovered
that the archived IB high/low and bid/ask close values may be
stored as strings. The research bridge formerly attempted
subtracting these raw strings, which the bridge must refuse.
The corrected bridge converts those validated numeric fields
to float exactly as the pinned original historical feature
builder does. Re-running all 1,001 sample points produced
zero feature mismatches and maximum absolute difference 0.
The original failed diagnostic remains retained, not erased.

  ~/.local/state/heptatrader/operator-evidence/laya-year-price-original-source-20261016/historical-feature-parity-v2.json

This is **feature math parity**, not proof that the original
sampled quote OHLC population matches revised IB bars; at
least one live standardized input coordinate still exceeded
training's +/-6 clamp. The historical 14-fold success must
therefore not override the first actual original-source
failed observation or the original 90-window acceptance rule.
