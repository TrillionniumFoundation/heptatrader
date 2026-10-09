# Training continuation — 2026-10-09

## Prospective Laya rule v2

Legacy rule v1, all original outcomes and spent alpha remain unchanged. Fixed 12
slots cannot reach alpha from trial 14 onward. V2 keeps alpha=0.05/(t*(t+1)) and
preplans the smallest non-tie sample with >=80% binomial design power at a 75%
win alternative; adds exactly two censor slots. At trial 29 this is 84 non-ties,
86 slots and a critical 60 wins at n=84. This is a design assumption, not a
market forecast or proof of independence. Conditional sign-null assumptions and
absence of unconditional efficacy are explicit. Only one v2 evaluation can be
pending. No retrospective rule change, count reset or censored-window replay.

Storage denial now publishes fresh status without consuming rows. Closed legacy
qualification counts remain in aggregate status after a source upgrade. External
wrapper errors also publish current failure status. CPU CI installs pinned torch
2.8.0 from the official CPU wheel index in a separate virtual environment; skip
rejection remains enabled. No local ROCm or trading environment upgrade is needed.

## USD100k Value continuation

The requested 30-row snapshot is distinct from the original frozen 11-row fit.
A new zero-initialized 769-parameter head refits all 30, not 19 incremental actor
updates. Steps=16, scale=USD100, norm cap=0.1 and serial frozen-backbone encoding
are unchanged. Validate every original input/binding/fee/settlement before and
after fit. Preserve original candidate/head bytes and negative labels.

The current100k forward plan uses its own schema, exact producer/model/period and
USD100k feature text. It must never use the old 25,000 EUR feature template. Reuse
the existing signed registrar/predictor/evaluator framework and bounded Huber
comparison only. Freeze two action-conditioned predictions before PREPARED and
first fill; record late/missing requests rather than backdating. Retain the
original 14-day input window plus one-day settlement grace, two baselines, cost
stress, temporal coverage, corrections and no-effect boundary. Continue candidate
alpha after the prior financial plan; no alpha reset. Future validation cannot
be claimed completed at registration. Inference is a bounded child under the
same heavy-worker lock, not another warm GPU owner.

## Neuron256 next hypothesis (not yet a new efficacy experiment)

Keep original v2 checkpoint/candidate and its rejection. In the same seven-bin
check set graph Brier 0.76788193 is worse than numeric 0.76409173, equal mix
0.76311743 and prior 0.72927856. Calibration/check diagnostic net is negative.
Neither the 3-class frozen campaign nor this already-inspected development set
is a new independent test for this seven-bin task.

H1: the 128-row graph-fit segment produces overconfident residuals around the
prior. Test a prior-anchored residual mixture p=(1-rho)*prior+rho*graph with
rho in {0,0.1,0.25,0.5,1}; choose rho only within a purged chronological internal
split of the original fitting role, not using economic calibration or check
labels. The rho=0 prior is a mandatory control, not a discarded non-model arm.
Keep 128/64 optimizer step budgets; do not expand training to mask this failure.
Freeze the choice before genuinely new future outcomes. Evaluate Brier and
calibration against numeric/equal-mix/prior on identical subjects.

H2: direction probability improvement may still be insufficient after actual
spread, commission and adverse slippage. Keep the original side prices and cost
assumptions, require positive out-of-fit calibrated expected net and retain
ABSTAIN when it fails. Report all frozen-policy trades including losses and
fixed-trade cost stress. Do not reduce costs or thresholds to admit this model.
Neither hypothesis is a positive result, promotion, or permission to trade.
