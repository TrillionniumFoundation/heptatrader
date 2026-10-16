# Laya price-head residual and causal-online baseline research

## Scope

Everything runs on Pocket4. The price baseline is research-only: it neither
trains the current Laya neural checkpoint nor changes formal 90-window
evaluation, original trial38, settled PnL, orders, model promotion, canonical
owner, or the existing Execution/Gateway/FX/BTC services.

The original failure is material, not merely a numeric difference in training
loss. On 120 historical day-3 observations, the original candidate's
seven-class price forecast achieved 5/120 Top-1, Brier 0.990804,
NLL 2.361728, versus a static training-only frequency baseline at 28/120,
Brier 0.754412, NLL 1.481955. The earlier source selected **zero** neural
probability weight on validation.

## Historical protocol and registered families

Original forward observations come from 37 closed Laya shadow trials; one
PENDING trial is excluded. Every observed prediction must precede its
original observed future outcome, have the matching candidate/plan/slot/anchor,
and match its SHA256 receipt. This provided 298 observations.

The split remains whole UTC days, never random shuffled:
- 2026-10-07: 62 training observations.
- 2026-10-08: 116 validation observations.
- 2026-10-09: 120 holdout observations.

The new source-only price residual module tests two fixed families,
each fitted **only** on day 1: smoothed conditional true-class
frequencies given the frozen model's predicted Top-1, and a bounded
label-prior/predicted-marginal ratio correction. The sources are either
the frozen parent or candidate price probabilities. None of the
candidate parameter families was selected over the static training
frequency prior by day-2 Brier. The explicit third-day result therefore
remained Brier 0.754412 and NLL 1.481955; do not retrofit parameters
from the third day. Report:
price-residual-conditional-20261016.json, report SHA-bound ID
58b5c5e3e63dc0837a72a3081d651e7515fe134a9cdf2924f991da7becbdf139.

## Strictly causal online baseline

A separate registered prequential family allows **online label updates
only after the original recorded outcome evaluation timestamp** and
only for price anchors earlier than the next prediction. It never
relies on a future trade close, end price, or retroactively rewritten
anchor. This is different from an untouched fixed-model holdout:
earlier day-3 outcomes genuinely available before later day-3 anchors
are allowed to update the prior.

The fixed grid consisted of half-lives 3, 6, 12, 24, 48 hours or no
decay, and prior strengths 1, 4, 16 or 64 against the day-1-smoothed
frequency prior. Whole day 2 selected a 24-hour half-life with prior
strength 1.0; day 3 was used only for reporting.

| Day-3 historical benchmark (120 rows) | Brier | NLL | Top-1 hits |
| --- | ---: | ---: | ---: |
| Frozen Laya candidate | 0.990804 | 2.361728 | 5 |
| Training-day static class prior | 0.754412 | 1.481955 | 28 |
| Validation-selected causal-online class prior | **0.684997** | **1.326042** | **58** |

The prequential prior predicted middle class 3 on all 120 third-day
rows. It has not demonstrated neural-feature-specific predictive power
or financial return. It is not a frozen independent 120-row inference
test because prior third-day labels are used strictly after availability.
The model selected from the day-2 validation, not by inspecting day 3.

Historical report:
price-prequential-20261016.json; immutable evaluation result ID
0762de1e5a8a397b95e8e77b7022c0e26409c2b5055b3e82d3eb591e92d6217c.

## Prospective, read-only 90-slot research cell

The isolated script at scripts/research_laya_price_shadow90.py registers a
fixed 90-slot future schedule before opening its first slot: one per six
minutes, five-minute return horizon, 60-second fixed input window, and
an 88-of-90 research input-coverage condition. This is a **different**
research cell, NOT the official Laya promotion acceptance protocol.
No order authority, broker calls, model update, or promotion exists.

The runner verifies the installed read-only feed configuration, original
period SHA256, the frozen historical report and prior parameters, and
the exact runner/source hashes. It freezes the 298 previously available
historical labels before any future slot. Each forecast is time-stamped,
input-anchored and written once before its outcome. Within the ongoing
research trial, *only actually completed earlier results with recorded
evaluation timestamps before the next anchor* may update the prior.

The future observed outcome is scored only from original quote receipts,
with the original exact anchor, five-minute endpoint bound and path-gap
condition. Missing input, late endpoint, tampering, source gap, or
no causal evidence remains CENSORED in the denominator. The fixed trial
continues recording even after reaching three censors, but never reports
a passed coverage result. It can resume its original plan after restart
and refuses changed config, report, or runner source pins.

This cell tests source-to-prediction-to-outcome continuity for a
non-neural baseline. **Its future coverage is not evidence that the neural
model met 88/90, that Laya qualified a promotion, that a strategy
outperforms an economic baseline, or that historical calibration has
a profitable execution policy.** Only a prospectively frozen neural
candidate with the same future anchors and complete independent
scoring can address that larger question.

All research files and receipts live under Pocket4
~/.local/state/heptatrader/operator-evidence/.
No part of the implementation should be installed into the trading
model-selection or order-execution service without separate proof.

## First actual future operational receipt (2026-10-16 UTC)

The bounded source-only 90-slot cell actually started on Pocket4 and
precommitted its first prediction 1.406 seconds after a real original quote
anchor, before a five-minute observed future outcome. A separate scorer
subsequently recorded slot 0 as OBSERVED with no source path gap. The
original quote showed -0.401129 bps (class 3). The as-of online prior
hit class 3, Brier 0.527522 and NLL 1.010038; the training-only static
prior missed, Brier 0.776295 and NLL 1.507901. The source-only
result is **1 OBSERVED / 0 CENSORED of 90 scheduled**, not a 90-slot
coverage pass, not a paired neural model comparison, and not proof of
statistical/economic performance. The runner has no trade authority.

Evidence is under
~/.local/state/heptatrader/operator-evidence/laya-price-shadow90-20261016/.
The first-slot independent hash and causal timing recheck is recorded in
~/.local/state/heptatrader/operator-evidence/laya-action-calibration-20261016/price-shadow90-first-future-proof.json.

A separate Pocket4 research probe evaluated a small, train-only standardized
16-feature seven-class logistic head on disjoint five-minute anchors from
previously recorded real PAPER datasets. It selected an L2-regularized
feature model on 51 validation examples and showed only a *small* Brier
improvement on 46 later holdout anchors (0.659359 static prior versus
0.656351 fitted model); NLL 1.287011 versus 1.280679, and both had
24/46 Top-1 hits. Those short, possibly correlated sessions are not
sufficient to promote another model. Its experiment receipt is kept outside
production as price-raw-feature-logistic-20261016.json.
