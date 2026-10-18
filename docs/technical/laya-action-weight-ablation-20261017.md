# Fixed historical action-weight ablation

This Pocket4-only research run compares action weights 0, 0.01 and one weight
frozen from training-only initialization gradients. It reuses the resident model,
raw price/action cross entropy, AdamW (0.0001, zero decay), global L2 clipping at
one and full-adapter/optimizer rollback on the existing norm limit.

The sealed original cohort contains 178 chronological training rows (9 ACT) and
120 previously examined historical holdout rows (5 ACT). The same proportional
approximation of the training pool is fixed for all three arms: 2 ACT and 30
ESCALATE, ordered chronologically, in 16 disjoint batches. Initial uniform temporal
subsampling omitted ACT entirely; this was detected before any holdout forward.
No holdout score chooses samples, temperature, gradient weight or extra steps.

All calibration batches use the same frozen initialization without optimizer
steps. The third arm targets a median action-to-price gradient norm ratio of one
on nondegenerate calibration batches. Disjoint heads are explicitly described as
a global clip-budget heuristic; degenerate batches remain in the receipt.
Training keeps raw logits. A single temperature from all 178 training rows is
used only for additional probability scoring, alongside raw log-softmax NLL.

Original live insertion order is restored only for direct replay of sealed rows.
All 298 parent rows are rechecked against their original probabilities, and the
existing B2 calibration forwards check frozen-parent correctness consistency.
Production rows_for_training already reconstructs causal_state in its original
order and does not require a change.

The active resource thresholds, manual hold, Value priority and original
heavy-worker lease are reused in a separately declared offline research scope.
The existing Landlock/seccomp sidecar sandbox is entered before Torch loading.
No live-source qualification, native candidate promotion, trading or financial
reward learning is granted by this experiment.

## Completed Pocket4 result

The durable job `1a14983c753-1123a8-3cba` and experiment systemd unit exited
successfully. The outer receipt records exit code 0 at 2026-10-17 11:07:22 UTC.
The measured experiment duration was 501.526 seconds, with 1,590,854,656 bytes
of peak GPU allocation. Each arm completed 16/16 updates; none was rolled back.

The frozen train-only weight is **0.0028313797014203035**. All 16 calibration
batches were usable. The initial median unweighted action/price gradient-norm
ratio was 353.1847034; its weighted value at 0.01 was 3.5318470.

### Mechanism finding

Every actual update had zero shared trainable gradient tensors, zero overlapping
gradient coordinates and clip factor one.

| Arm | Maximum gradient norm | Applied / attempted | Rollbacks |
| --- | ---: | ---: | ---: |
| Price only, weight 0 | 0.2697220743 | 16 / 16 | 0 |
| Current weight 0.01 | 0.5660214424 | 16 / 16 | 0 |
| Gradient-derived weight 0.00283138 | 0.3043051064 | 16 / 16 | 0 |

Each price branch changed 2,212 factor coordinates from initialization, with
factor-delta L2 0.04649863924. All four price adapter tensor payloads are
**byte-for-byte identical across the three arms**, independently verified from
saved safetensors without Torch. The price branch did train, but changing
action weight did not change its updates in this experiment.

The two nonzero-weight action adapters differ in 37 coordinates; their direct
factor-difference L2 is 0.0001885400261. Their saved holdout action logits and
price probabilities are nevertheless exactly equal at recorded precision.
They are distinct parameter artifacts, not identical models.

These observations reject the proposed explanation that the roughly 90%
scalar action-loss share suppressed price learning in this run. They do not
establish invariance for shared trainable backbones, clipping, rollback,
dynamic weights, different optimizers or optimizer histories. AdamW's moment
normalization can approximately cancel a constant positive gradient scale
when epsilon is small. This explains the measured near-invariance under the
present conditions; loss weights are not universally irrelevant.

### Historical predictive quality

All scores use the same 120 previously examined historical holdout rows.
NLL and Brier are lower-is-better. Brier sums seven price classes or two action
classes before averaging rows. The prior uses only the 178 training labels
and adds 0.5 per class.

| Model / baseline | Price NLL | Price Brier | Price correct | Raw action NLL | Raw action Brier |
| --- | ---: | ---: | ---: | ---: | ---: |
| Frozen parent | 2.361973170 | 0.990881385 | 5 / 120 | 2532.572621663 | 1.916666667 |
| Price only | 2.361784179 | 0.990821961 | 5 / 120 | 2532.572620646 | 1.916666667 |
| Weight 0.01 | 2.361784179 | 0.990821961 | 5 / 120 | 2532.450558472 | 1.916666667 |
| Weight 0.00283138 | 2.361784179 | 0.990821961 | 5 / 120 | 2532.450558472 | 1.916666667 |
| Training class prior | **1.375646088** | **0.703504618** | **58 / 120** | **0.174597926** | **0.080121303** |

Price NLL improves from the parent by only 0.0001889905; no argmax
classification changes. The model remains worse than the training prior.

| Price class index | Training labels | Holdout labels | Parent and every trained arm predictions |
| --- | ---: | ---: | ---: |
| 0 | 0 | 0 | 70 |
| 1 | 20 | 8 | 0 |
| 2 | 44 | 28 | 7 |
| 3 | 59 | 58 | 2 |
| 4 | 36 | 23 | 19 |
| 5 | 19 | 3 | 15 |
| 6 | 0 | 0 | 7 |

In this cohort, 77/120 predictions fall in two classes with no training or
holdout observations. This diagnoses a distribution/readout problem; it does
not prove that label indices are incorrectly mapped.

The trained nonzero-weight arms have raw ACT-minus-ESCALATE margins from
2504.90784 to 2800.22351, median 2645.05829. ACT probability is one at stored
precision on every row. At both raw thresholds 0.5 and 5/6 the confusion
counts are TP 5, FP 115, TN 0, FN 0. These labels denote frozen-parent price
correctness, not broker orders or trading actions.

The pre-frozen training-margin p90 temperature is 2711.232788. It gives the
nonzero arms action NLL 1.254501352 and Brier 1.016819349, with ACT probability
0.715836 to 0.737463. At threshold 0.5 all rows still classify ACT; at 5/6
none do. Positive temperature scaling cannot change margin sign. This is
only a score diagnostic: it neither resolves the base-rate mismatch, beats
the prior nor demonstrates independently validated calibration. Saturated
probabilities alone also do not establish absence of ranking information.

### Reproducible CPU diagnostics

`laya_cell.resident_action_ablation_report` re-scores saved logits and
probabilities, verifies source/profile/adapter hashes and row alignment,
reproduces recorded aggregate scores, computes per-class and threshold
tables, and compares tensor payloads without Torch. It separates mechanical
completion, the gradient hypothesis, historical quality and production
eligibility. It starts no forward pass, training job or runtime operation.

Five focused tests cover uncapped large-margin NLL, temperature versus
classification, rejected row reordering, multiclass Brier conventions and
invalid/nonfinite inputs. All passed on Pocket4. The original gradient
diagnostic module's three tests passed before the real GPU run.

To reproduce, run `python3 -B -m laya_cell.resident_action_ablation_report`
from this worktree on Pocket4 with `PYTHONPATH=research/neuron_learning`:

- `--result-directory`: the evidence root below plus `candidates/three-arm-v4`.
- `--source`: `/home/alex/.local/state/heptatrader/operator-evidence/laya-original-quote-asof-forensics-20261016/action-v2-real-closed-parent298-trainonly.json`.
- `--output`: a new file under the same Pocket4 evidence root.

The recorded report is `posthoc-diagnostics.json`. Original model, data,
logs and per-row artifacts remain on Pocket4.

### Evidence and cleanup

Evidence root:
`/home/alex/.local/state/heptatrader/operator-evidence/train-action-ablation-20261017`

| Receipt | SHA256 |
| --- | --- |
| `candidates/three-arm-v4/result.json` | `526cccd03be188d80e391d66854785d4ea46d337351c219c0f058006b913a898` |
| `candidates/three-arm-v4/frozen-train-profile.json` | `1349f00ae99bdf27562b411471d9e0e210112dab6c2bb406a5261d3d00da509e` |
| `posthoc-diagnostics.json` | `0bb58baa69e53a0dba9c2caab016001b0c7c484ec725c65d07f98dddbe9002ea` |
| `action-cache-cleanup.json` | `ac5598379c9db35c5d0c74d2d5faa04746efca0493dd6618cb6be91b2de1ed5b` |

After authoritative exit, cleanup reacquired the original global heavy-worker
lease, confirmed an empty experiment cgroup and no active references across
121 readable processes, and removed only 48 reproducible COMGR files totaling
159,618,340 bytes. All 13 non-COMGR files/entries retained identical hashes.
Model adapters, predictions, scores and prior admission-denial records remain.

### Resulting development priority

This experiment closes the action-weight suppression hypothesis for this
fixed run. It supplies no promotion, fresh prospective evidence or financial
reward learning. Lowering the scalar action weight is not a supported next
optimization.

Next, bind label/index and parent identities, then measure inherited readout
scale, bias and effective adapter movement on training inputs. Compare readout
learning with simple supervised and training-prior baselines using a new time
split. Freeze preprocessing, calibration and tuning using training/development
data before assessing an independent future cohort. The present reused holdout
must not choose the next weight, temperature, threshold, steps or candidate.

## Completed readout follow-up — 2026-10-18

The subsequent [training-only readout diagnosis and prior control](laya-readout-diagnostics-20261018.md) now measures hidden-state scale and frozen-weight contributions. It explains the saturated action margin, proves the fixed-representation limitation of the current adapter budget, and reports a separate normalization plus fixed training-prior intercept control. The previous ablation and its historical holdout results above remain unchanged.
