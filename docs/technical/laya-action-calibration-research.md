# Laya ACT/ESCALATE calibration — Pocket4 research only

## Problem and measured cause

ACT in this module means confidence in the frozen predecessor's seven-bin
interval forecast, **not** BUY/SELL and not permission to submit orders.
No trading, canonical owner, current evaluation rule, or frozen model
is changed by this work.

Read-only inspection found 298 original OBSERVED outcomes in 37 closed
plans, dated Oct 7–9, 2026 UTC, and excluded one pending trial.
The parent was correct only 14 times (4.7%). The existing action head
assigned ACT probability 1.0 to all 298 forecasts. Its average Brier
loss was 1.90604 versus 0.09396 for always ESCALATE.

The actual Pocket4 gfx1150 read-only GPU diagnostic on 32 real mature
training rows measured ACT logits +1,148.89 to +1,228.83 and ESCALATE
logits -1,470.18 to -1,377.92, producing a gap of +2,526.81 to
+2,698.62. All 32 targets were ESCALATE. Weighted action gradient
norm was 0.31810 versus price-gradient norm 0.14064; combined norm
was only 0.34781, below the existing 1.0 cap. The scorer and action
adapters had distinct trainable parameters in this sample, so the
numeric auxiliary loss alone does not prove price-head damage.

GPU evidence:
~/.local/state/heptatrader/operator-evidence/laya-action-calibration-20261016/action-logits-gradient.json

## Fixed chronological calibration design

The read-only source module is at
research/neuron_learning/laya_cell/action_quality_research.py;
the reproducible CLI is scripts/research_laya_action_quality.py.
They verify plan/qualification identity, prediction/outcome hashes,
causal timestamps, target alignment, probability normalization,
duplicate anchors, and exclusion of pending formal trials.

The 298 old observations were separated by complete UTC date:

| Training | Validation | Independent test |
| --- | --- | --- |
| Oct 7: 62 rows | Oct 8: 116 rows | Oct 9: 120 rows |

Train-only smoothed confidence priors with 1, 2 or 4 bins were compared
with always ESCALATE. Validation Brier, never the third-day test,
selected four bins, with fitted parent-correct probabilities
0.09677, 0.06250, 0.03226 and 0.03125.

| Third-day 120-row test | Brier, lower is better |
| --- | ---: |
| Original saturated action head | 1.916667 |
| Always ESCALATE | 0.083333 |
| One-bin train-only prior | 0.080046 |
| Two-bin train-only prior | 0.077297 |
| Four-bin model chosen on validation | 0.077418 |

The two-bin result is reported but must not be substituted after
observing its test score. **All** calibrated models suggested ACT
zero times. Thus the observed improvement is probability calibration,
NOT proof of useful action discrimination or economic improvement.

The profile's proxy costs (wrong ACT 3, ESCALATE 0.5) imply that
parent correctness must exceed 5/6 before suggesting ACT.
No fitted bin qualifies. This is not execution PnL.

Historical report ID:
9754f43971ffbaea07b510b1513c076034b39544866005eb180ff160afe5f56c
Selected research model ID:
5c1579cc6380b7c1ab34e190ccc7ce01b24586cbb76043aa908ec09b833e9f54

## Deployment and next evidence

This module has no optimizer, no model promotion, no order/driver APIs,
and no registration into the real Laya evaluation queue. It preserves
the active trial38 cutoff, all previously missed outcomes, and
the original 88/90 future-coverage requirement. No original production
action logits or predictions have been replaced by these estimates.

A future version must make an immutable pre-event calibration seal,
prove time-valid real original-source predictions and delayed outcome
observations, retain every censored window, and compare with the
always-ESCALATE baseline prospectively. The three historical dates
alone cannot establish statistical or financial efficacy.

A proposed prospective runner was not executed or authorized; its
unexecuted source is archived separately in operator evidence rather
than deployed into the trading environment. All HeptaTrader
training and execution remain on Pocket4.
