# Pocket4 Action V2 original-source fixed historical confidence baseline

Research-only. This completed **CPU** control is not Laya Action V2 LoRA
fitting, prospective model evidence, trading authority, or an order.
The real Pocket4 Trainer/Evaluator, Execution, Gateway, FX/BTC drivers,
frozen trials and historical files were not changed.

## Bound original source and causal cohort

Original private source:
  ~/.local/state/heptatrader/operator-evidence/laya-original-quote-asof-forensics-20261016/action-v2-real-closed-parent298-trainonly.json

SHA256:
  3d701e3795cb438b5ba38f3bb7c44bed83003e040108d1378d1084635157e4a9

The sealed original-source population has 298 distinct rows from the same
frozen predecessor, 14 genuine correct ACT and 284 wrong ESCALATE labels.
The reader verifies the original manifest and row digest and refuses
duplicate identities, changed target consistency, probabilities outside
the seven-class simplex, and late/foreign training rows.

One **hard-coded** cut: training anchor and original outcome available before
2026-10-09 00:00 UTC, and separate 2026-10-09 anchor-only holdout.
Train 178 (9 ACT, 169 ESCALATE); holdout 120 (5 ACT, 115 ESCALATE).
No holdout label chooses the normalizer, learner weights, hyperparameters
or train prior. The dates are previously analyzed, not a new future cohort.

## Three exactly matched inputs

All methods use the frozen predecessor's already-sealed seven-class
probability vector. ACT refers to original *seven-class correctness*,
NOT broker BUY/SELL, action selection or PnL.

1. Raw frozen predecessor maximum seven-class confidence as an
   uncalibrated diagnostic proxy for probability of correct ACT.
2. The training-only Jeffreys correctness frequency (9 + 0.5) / 179.
3. Exactly 300 deterministic full-batch **CPU** logistic gradient-descent updates
   on the earlier original confidences, with train-only feature
   standardization, 2 parameters, learning rate 0.05 and L2 slope
   coefficient 0.1. No inverse class weights or test-based selection.

The threshold of 5/6 is the historical correctness/escalate *proxy*,
not a measured execution-profit threshold.

## Actually executed Pocket4 Oct 16, 2026

| Historical Oct 9, n=120 | Binary Brier | Binary NLL | ACT proposals at 5/6 |
| --- | ---: | ---: | ---: |
| Raw parent confidence | 0.26666956 | 0.44797949 | 1 |
| Train-only class prior | 0.08012130 | 0.17459793 | 0 |
| CPU logistic | 0.07933405 | 0.17058073 | 0 |

Incremental Brier difference compared with prior is about 0.00078725,
too small to establish independent predictive value given only five
positive held-out cases. The fitted CPU control offers no new ACT
suggestions, has no full Action V2 GPU LoRA updates and is not proof
of model or economic improvement.

The original create-only receipt remains on Pocket4:
  ~/.local/state/heptatrader/operator-evidence/laya-action-v2-frozen-confidence-20261016/first-history-split-v1.json

Receipt SHA256:
  6dad7e19623a77b4d10c8b23879466f5fd4df0b9fb83426492d7b0f56e11b672

The output binds source/row manifest, fitted coefficients, full train
and heldout aggregates and exact program SHA. No private trading
records or model tensors are committed to the public repository.

## Run and limitations

In the source checkout on Pocket4, use a distinct pre-existing owner-only
evidence output directory for each new O_EXCL experiment:

    PYTHONPATH=research/neuron_learning python -B -m laya_cell.action_v2_frozen_baseline \
      --source /home/alex/.local/state/heptatrader/operator-evidence/laya-original-quote-asof-forensics-20261016/action-v2-real-closed-parent298-trainonly.json \
      --expected-source-sha256 3d701e3795cb438b5ba38f3bb7c44bed83003e040108d1378d1084635157e4a9 \
      --output /home/alex/.local/state/heptatrader/operator-evidence/NEW_PRIVATE_DIR/report.json

For full future Action V2 adapter research, first obtain **real**
frozen-parent action logits from the actual pinned predecessor and
use correctly equal-weighted, time-matched two-class updates from
draft PR #378. The actual Pocket4 heavy-worker Guard separately
refused new work for a fresh original Value request and elevated
memory PSI (60s full memory 0.27 above 0.10). Never bypass this guard
or move HeptaTrader off Pocket4.

Future true independent pre-event predictions and outcome settlements
are still required. Prediction quality never alone confers broker,
canonical owner or model promotion authority.
