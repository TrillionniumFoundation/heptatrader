# Pocket4 Laya ACT/ESCALATE v2: isolated training research

> Historical research record: the state descriptions below refer to the
> original trial39/v2 study and its frozen evidence. They are not a live-service
> status report. The later v3 objective corrects double balancing only for new
> hash-bound profiles and adds a 300-second prospective baseline rule; see
> [the versioned resident policy](../../research/neuron_learning/laya_cell/RESIDENT.md#versioned-training-policy-and-the-300-second-experiment).
> Existing candidate, calibration and qualification evidence is preserved.

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


## Real Pocket4 frozen-parent CPU Logit replay (no optimizer)

A further model-level test verified the *actual* v1 frozen predecessor
rather than inferring raw logits from saturated saved probability vectors.
On Pocket4, using the original SHA-pinned train job config and all 32
exactly registered, genuinely mature training rows, the CPU model
loaded the original 644 MB pretrained weights and froze the stored
parent adapter. No GPU, optimizer step or trial write was performed.

Results: frozen parent ACT top-1 **32/32**, genuine labels ACT:ESCALATE
**0:32**. Actual ACT-minus-ESCALATE raw Logit margin:
min **2,526.815**, mean **2,627.890**, max **2,698.618**.
Absolute raw-margin **training-only P90 = 2,672.294**.
The recomputed raw CE mean **2,627.890** closely matches the
original frozen v1 training-step action loss ~2,627.841.

Immutable local evidence:
~/.local/state/heptatrader/operator-evidence/laya-original-quote-asof-forensics-20261016/action-v2-real-parent-cpu-32row-logits.json

This *still does not admit action training*: the real parent label
distribution is one-class. v2 must not refit from trial39 pending
evaluation results and must not substitute synthetic ACT labels.

## v2 frozen train/inference temperature coherence

Additional NONPRODUCTION source-only research function:
resident_action_v2_research.v2_action_inference.

If a separate future training population has enough genuine
ACT and ESCALATE examples, the inference function consumes the exact
hash-bound, training-only P90 temperature used by the action CE.
It normalizes raw ACT/ESCALATE logits with that temperature in
float64, preserving very large common logit offsets. It emits
an explicit *research-only* ACT proposal only when normalized
correctness probability meets the already declared cost-based
5/6 threshold; it never grants execution or model authority.

If action optimization was refused for the genuine one-class cohort,
the function returns **NO action probabilities** and only
ESCALATE_ONLY_NO_BALANCED_TRAINING. If future raw margins exceed
10 times the frozen training margin, it ABSTAINS on those rows.
Replacing the frozen temperature after fitting fails closed.
**Temperature scaling by itself is not validated probability
calibration**: it still needs a separate genuinely future outcome
study. Current v1 raw output, 90-window research, trial39 and
execution remain untouched.

28 synthetic adversarial CPU tests exercise these boundaries:
7 new cases cover train-versus-inference temperature equality,
single-class abstention, out-of-distribution abstention, spoofed
temperature, extreme common offsets, absence of trade permission,
and finite/bounded inputs. The v2 implementation has NOT been
wired into the running original v1 prediction or training service.

## Real future-v2 training-only source now frozen on Pocket4

To avoid artificial ACT class balancing and future label leakage,
we audited **all 38 closed historical Laya evaluation plans** using
their actual SHA-bound prediction/outcome files. Exactly
**298 genuine OBSERVED future results** came from **26 closed plans**
and **all share the same frozen predecessor identity**:
76d6b1bf3d1b00505277c88a6d93159f3f17fe2511a65f21fc99647cf56b8ff6.
The true frozen-parent correctness labels were **14 ACT and
284 ESCALATE**. The one still-live trial39 was wholly excluded.

An immutable data-only frozen candidate is now written to:
~/.local/state/heptatrader/operator-evidence/laya-original-quote-asof-forensics-20261016/action-v2-real-closed-parent298-trainonly.json

Precommitted training cutoff: **1792144194352 milliseconds**,
the creation timestamp of the already existing trial39 plan. All
298 original source outcomes were available and CLOSED by this
cutoff; later future examples cannot enter this training file.
The hash-bound training-population manifest is:
a6457e18d362989fda2cacb110550538ac985508106439b20d1f4608c631fab3.
Its canonical row population digest is:
0ccc69e270f6d5159b63edf80e9f815acf472019761e9fad813a37b3ff20c589.

The reusable read-only collector (code-only and run without
GPU or trial mutation) is:
research/neuron_learning/laya_cell/resident_action_v2_closed_source.py
and scripts/research_laya_action_v2_closed_train_source.py.
It checks plan IDs and final qualification identities, source cutoff,
genuine parent ID, original prediction file SHA, quote input identity
and chronology, valid original bid/ask and closing midpoint, exact
seven-bin future price label, parent Brier, original Action Brier,
and duplicate anchor exclusion. It seals the complete original input,
future label and per-record SHA evidence under one immutable digest.

This source opens a genuine ACT/ESCALATE two-class v2 *training
possibility* without taking any labels from trial39. It is
**not proof of model efficacy or a trained adapter**.
The original 32-row trial39 gradient population remains 0:32 and
is never reclassified or rewritten. The new 298-row population
must only train a NEW explicitly versioned model and cannot be
scored against those same 298 rows as prospective validation.
A new independently precommitted market future window is required.
Neither v1 trainer nor currently running evaluation/Shadow unit
was modified. No new v2 GPU fitting or new model authority claimed.

11 new adversarial source tests cover original price label and
quote authenticity, fixed training cutoff, different parent identity,
missing/tampered prediction bytes, duplicate anchor, pending-plan
exclusion, signed row-digest identity and the genuine two-class
16-example synthetic source case.
