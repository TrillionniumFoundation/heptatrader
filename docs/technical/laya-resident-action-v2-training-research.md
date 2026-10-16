# Pocket4 Laya ACT/ESCALATE v2: isolated training research

## Current state and scope

NONPRODUCTION RESEARCH SOURCE. This is a numerical successor plan for
FUTURE separately registered Laya candidates. It does not hot-patch the
current resident_train objective, evaluator, execution driver, frozen
trial39 model, current Tiny MLP Shadow 90 or original price-prior Shadow 90.
All training and evidence remain on Pocket4.

ACT/ESCALATE means answer-versus-escalate decision support. It is NOT
BUY/SELL, broker execution authority, or a policy gradient derived from PnL.

## Real frozen trial39 findings

Frozen candidate ID:
387481b75280ce115161949ba2e2cd5ab8cef7770e9f1ecfe4d0b096543d56a6

Candidate manifest SHA256:
181d9fa606dc697d890f4272fc852646af54fb4451be0f2f114e9738f44aeb13

- 32 uniquely applied real training rows and 16 applied distinct
  optimizer gradients; 2,251 changed LoRA coordinates of 2,379
  trainable; no PnL-based gradient credit.
- Training truth for FROZEN parent correctness: 0 ACT and 32
  ESCALATE. All 16 action optimizer steps executed despite having
  no ACT examples.
- Raw Action Loss averaged 2,627.84. At nominal weight 0.01, its
  contribution was 91.3% of the observed numerical total loss.
  This is not proof of meaningful two-class learning.
- The first 14 observed real trial39 outcomes yielded 14/14 ACT
  suggestions, but only 2/14 parent predictions were correct.
  Action Head Brier 1.7143. Candidate-vs-parent price Brier gain
  ~0.0000652 on these 14 still-pending-trial slots. None were used
  for new calibration or model selection.

## Closed-data chronological calibration research

The existing closed-trial, read-only action_quality_research program
was run on Pocket4. It validated 298 truly observed CLOSED examples,
chronologically split 62 train / 116 validation / 120 independent test.
The frozen parent was right on only 5/120 held-out examples.
Original saturated action head: 120/120 ACT, Brier 1.91667,
wrong-answer/escalation proxy cost 2.875.
Always ESCALATE: Brier 0.08333, proxy cost 0.5.
Frozen validation-selected four-bin calibration:
held-out Brier 0.07742; still suggested ESCALATE for all 120.

This retrospective result neither qualifies a model for production nor
demonstrates financial return. Pending trial39 is excluded by design.

Evidence on Pocket4:
~/.local/state/heptatrader/operator-evidence/laya-original-quote-asof-forensics-20261016/action-calibration-closed-trials-20261016-next.json

## Future-only v2 action optimization

Implemented as an independent pure research module:
research/neuron_learning/laya_cell/resident_action_v2_research.py

- Only genuine original, distinct frozen training rows whose future
  outcomes became available by a fixed train cutoff may supply
  correctness labels. No later trial39 labels, duplicate rows, or
  reinterpreted future outcomes.
- Require at least 16 rows and four true samples of EACH ACT and
  ESCALATE before admitting an Action Head update. Frozen 32-row
  trial39 training batch thus REFUSES action optimization. The
  independent PRICE loss may continue learning genuine rows.
- For balanced training support, pair disjoint genuine ACT and
  ESCALATE rows. Excess one-class rows remain eligible for PRICE,
  but cannot substitute for absent action classes.
- Freeze temperature from TRAINING-ONLY P90 absolute raw parent
  action-logit margin (min 1, maximum 1e6). Scale logits for future
  action loss, rather than changing already-frozen v1 action output.
- Cap action coefficient at .1 and numerical action contribution
  at 20% of the primary price loss, with detached scaling. Test
  extreme saturated margins with finite, nonzero action gradients.
- Any proposed action recommendation stays RESEARCH ONLY, with
  model_or_trade_permission false. Unsupported population always
  suggests ESCALATE_ONLY_NO_BALANCED_TRAINING. This is not a
  broker authority override.

No new trained adapter, v2 production model, or future v2 Laya
trial has been launched. This research code intentionally leaves
running frozen v1 source/runtime, training config and model untouched.
A later explicitly versioned, train-only-normalized v2 model requires
actual Pocket4 GPU fitting, frozen manifests, future original-source
predictions, and independent performance evidence.

## Real preflight proof

Read-only audit runner:
scripts/research_laya_action_v2_preflight.py

Original-source and training receipt:
~/.local/state/heptatrader/operator-evidence/laya-original-quote-asof-forensics-20261016/resident-action-v2-real-training-and-future-audit.json

The auditor independently checked the candidate SHA, applied row set,
optimizer receipts, recorded 0:32 labels, 91.3% action-loss dominance,
the closed 298-example retrospective study, and trial39 pending
forecast hashes without using its outcomes to refit a new model.
The result is DO_NOT_OPTIMIZE_ACTION_HEAD_WITH_SINGLE_LABEL;
PRICE_ONLY_REMAINS_POSSIBLE.
