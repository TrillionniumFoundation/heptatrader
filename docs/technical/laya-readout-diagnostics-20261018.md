# Laya action readout diagnosis and training-prior control

Pocket4 research snapshot: **2026-10-18 02:58 UTC / 2026-10-17 22:58 America/New_York**.

## Outcome

The fixed training diagnosis locates the saturated action output in the frozen
readout path. CLS scale increases through the two transformer head blocks. Its
contribution dominates the first action linear layer, and the frozen final
weights produce an ACT-minus-ESCALATE margin around 2647. Changing the action loss
weight alone did not address this mechanism in the
[previous ablation](laya-action-weight-ablation-20261017.md).

Affine-free normalization of CLS reduces the mean margin to about 31, but leaves
all 32 examples saturated toward ACT. With these representations fixed, the
existing action adapter support and norm budget cannot remove the remaining
margin. A separate, fixed intercept aligned to the previously frozen training
prior removes saturation. Almost all of that improvement exists before its
16 optimizer steps.

This is a completed **training-only readout control**, not evidence of prospective
prediction quality or profitable trading. The new recipe includes normalization,
an intercept and adapter weights. It is not a legacy resident candidate and is
not registered for automatic selection.

## Scope and fixed identities

All project execution, data, artifacts and file operations in this round remained
on Pocket4. Source synchronization used GitHub from Pocket4. No other project host
was used.

The source cohort was already sealed: 178 chronological training rows, including
9 ACT labels, and 120 previously examined historical holdout rows. This round
reused exactly the same selected 32 training rows and 16 ordered two-row batches
from the prior ablation: **2 ACT and 30 ESCALATE**. There were no old holdout
forwards or new prospective observations in these experiments.

ACT is class 0 and ESCALATE is class 1. The action label denotes whether the frozen
parent's price answer was correct; it does not authorize a trade or measure PnL.
The full training-pool prior is (9 + 0.5) / (178 + 1), or
**0.05307262569832402**. Its target differs deliberately from the selected subset's
2/32 empirical fraction.

| Identity | SHA256 / value |
|---|---|
| Original source | `3d701e3795cb438b5ba38f3bb7c44bed83003e040108d1378d1084635157e4a9` |
| Fixed 32-row subset | `915803497a24dd66c0d211e043adb1d968f00cf33f159d7d44bd958b0cdc50cd` |
| Ordered 16 batches | `2fae24e49bedf61a61d241fb9a8fce7491f76d1e6b34500b25c696d1ce5d90fa` |
| Frozen parent | `76d6b1bf3d1b00505277c88a6d93159f3f17fe2511a65f21fc99647cf56b8ff6` |
| Complete original adapter initialization | `ce1b71844641b22397dc7189823c636946c623ccb000158432a559956615c9d1` |
| Action-only A/B initialization, identical in all three controls | `31fbff99ed1e1a9c1b1a83798d250741f71cec8dc7a2b6e08b8e6d8021e2a4ab` |
| Training cutoff, milliseconds since epoch | `1791504000000` |

The complete and action-only initialization hashes cover different tensor sets.
Their different values are expected.

## Instrumented forward and observed mechanism

The diagnosis ran on the installed Laya model with Torch 2.11.0+rocm7.13.0,
HIP 7.13.99004 and the Pocket4 gfx1150 GPU. Forward arithmetic was FP32, eval mode,
with no autocast or optimizer steps. It captured 32 training forwards and ran two
additional plain forwards for hook parity. It captured detached copies from the same
forward rather than substituting hook outputs.

The model encodes the serialized question, adds a type embedding, then applies
two norm-first, batch-first transformer head blocks. The price path gathers seven
marker representations and applies LayerNorm before its readout. The action path
concatenates the 768-dimensional CLS representation with four detached price
features, then applies Linear(772,256), GELU and Linear(256,2).

Both an ACT and an ESCALATE representative were checked with and without hooks.
Observed maximum discrepancies were **9.69e-8 in price probability** and
**0.0001221 in action logits**. The FP64 diagnostic reconstructions differed from
the actual FP32 outputs by at most **0.001107 before GELU** and **0.000661 in the
final margin**. These are the measured errors, not merely the 0.05 rejection
threshold. Frozen base and adapter tensor identities did not change.

### Scale and contribution measurements

Standard deviations below flatten each named tensor across the fixed training
trace; price-marker and CLS tensors cover different positions and shapes.

| Captured tensor | Standard deviation |
|---|---:|
| Encoder CLS | 1.760045 |
| Type embedding | 0.923195 |
| CLS after transformer head 0 | 25.526336 |
| CLS after transformer head 1 | 85.539239 |
| Price LayerNorm input | 203.906362 |
| Price LayerNorm output | 0.951763 |
| Action first linear output, before GELU | 331.875954 |
| Action final linear input, after GELU | 255.049592 |

The first action linear layer's preactivation decomposes linearly. Aggregated L2
contributions across its 32 by 256 outputs were **30,955.812 from CLS**, **1.754
from the four price features**, and **1.849 from bias**. This decomposition is
made before GELU; it does not assume that nonlinear outputs add independently.

The final ACT-minus-ESCALATE margin averaged **2647.271107**, with range
**2500.598267 to 2767.381958**. Its mean decomposition was:

| Final margin contribution | Mean |
|---|---:|
| Frozen base weights in the first 64 columns | 618.123452 |
| Frozen base weights in the other 192 columns | 2029.103637 |
| Frozen bias | 0.044022 |
| Initial action adapter | 0 |

About 76.65% of the mean frozen-weight contribution lies outside the current
adapter's 64-column support. The action adapter starts with B equal to zero and
has 66 trainable factor parameters.

Input reconstruction and the seven price-bin/marker mappings were checked.
Prices in these rows span 1.1174 to 1.12167, with observed state offsets from
-331297 to 0 milliseconds. State values enter serialized text/token IDs, not as
raw numeric timestamps directly concatenated into the action vector. The
diagnosis found no swapped ACT/ESCALATE indexes or price-bin index swap. These
checks do not establish that the learned price representation is useful.

## Three controlled readout experiments

The controls reused the captured action input and frozen price logits; they
performed **zero additional encoder forwards**. Each restored the same action A/B
initialization and used a fresh AdamW optimizer: learning rate 0.0001, no weight
decay, action loss weight 0.01, L2 gradient clip at 1, and 16 two-row steps.
The per-layer relative effective-weight limit stayed 0.005. The global limit
stayed 0.0025, using the original denominator across all three resident adapter
base matrices, including frozen price matrices. Optimizer rollback snapshots
were deep copies.

Each arm applied **16/16 steps with zero rollback**. All readout base weights
remained unchanged. Row ordering, labels, price logits and action initialization
were subsequently checked across all three arms. Raw step-zero replay and the
third arm's normalized step-zero replay both had maximum absolute error **0**.

All scores below are on the same selected training rows. NLL uses unbounded,
stable log-softmax calculations on raw logits; probabilities are not clipped to
hide saturation. Action Brier is the mean sum of squared errors over **both**
classes, twice the convention that scores only the ACT probability.

| Readout / stage | Mean action margin | Action NLL | Action Brier | ACT at 0.5 | FP32 p(ACT)=1 |
|---|---:|---:|---:|---:|---:|
| Raw CLS, step 0 | 2647.271107 | 2480.159733 | 1.875000 | 32/32 | 32/32 |
| Raw CLS, step 16 | 2647.143543 | 2480.040218 | 1.875000 | 32/32 | 32/32 |
| Normalized CLS, step 0 | 30.991924 | 29.052354 | 1.875000 | 32/32 | 32/32 |
| Normalized CLS, step 16 | 30.990417 | 29.050942 | 1.875000 | 32/32 | 32/32 |
| Normalized CLS + fixed prior intercept, step 0 | -2.881970 | 0.2320832416 | 0.1168470165 | 0/32 | 0/32 |
| Normalized CLS + fixed prior intercept, step 16 | -2.881935 | 0.2320829371 | 0.1168469394 | 0/32 | 0/32 |
| Constant frozen training-pool prior | -2.881561 | 0.2346304499 | 0.1173652508 | 0/32 | 0/32 |

Counts at the 5/6 ACT threshold equal those at 0.5 for every row of this table.

### Why more steps cannot remove the fixed-representation saturation

For a captured hidden vector h and any allowed action update Delta W, the
Cauchy-Schwarz bound gives

    |Delta margin_i| <= sqrt(2) * ||Delta W||_F * ||h_i[:64]||_2.

The frozen final weight norm is **0.8278413909**, so the per-layer limit permits
at most **0.004139206955** of effective Delta W norm. Because the initial action
B is zero, this bound is relative to the frozen parent. It is also an upper bound
for the rank-one adapter; relaxing the rank restriction cannot weaken the
impossibility conclusion.

| Fixed representation | Per-row maximum possible margin correction | Lowest remaining margin lower bound |
|---|---:|---:|
| Raw CLS | 13.6151 to 15.0792 | 2486.9832 |
| Normalized CLS | 0.167913 to 0.169056 | 30.766756 |

Every row remains strongly ACT under either bound. Thus the failure is not
explained only by stopping these controls at step 16: the existing fixed hidden
states, support and norm limit cannot remove their saturation with more steps.
This statement is conditional on those fixed representations. It does not prove
impossibility for an altered encoder, hidden layer, bias, support or joint price
training.

### What the fixed intercept accomplished

Normalization uses affine-free LayerNorm on only the 768 CLS coordinates, with
epsilon 1e-5. The original four price features remain bitwise unchanged.

The third plan froze a single additional intercept before optimization. On the
32 normalized training margins m, it solved

    mean(sigmoid(m + b)) = 9.5 / 179

with 80 FP64 bisection iterations, giving **b = -33.873892842640856**. Logits receive
the symmetric adjustment **[-16.936946421320428, +16.936946421320428]**. The slope
is one, and b stays fixed through all 16 steps.

The solve did not use individual row labels, but its target does use aggregated
labels from the 178-row training pool. It is therefore training-prior mean
alignment, not a label-free operation or proof of general probability
calibration. Matching the subset mean does not establish whole-pool,
conditional or future calibration.

The FP64 equation residual was **-2.36e-16**. Actual FP32 application gave a mean
of **0.053072579205**, about **4.65e-8 below** the target. There were zero changes
to pairwise ranks or ties at step zero. This intercept did not create a new AUC
gain. Normalized margins already had a training-only AUC of 0.9, compared with
0.716667 for raw CLS; that remained 0.9 after the fixed shift and after training.

Only two ACT rows underlie this AUC. Its 60 positive-negative pairs share those
two positives and are not 60 independent observations.

Before optimizer steps, p(ACT) ranged from **0.0502523 to 0.0558833**. After 16
steps, it ranged from **0.0502540 to 0.0558852**, averaging **0.05307435**. There
were no probabilities rounded to zero or one. All rows still classified as
ESCALATE at both reported thresholds. The resulting 30/32 action classification
accuracy equals the all-ESCALATE baseline and is not evidence of useful ACT
identification.

Compared with the constant prior, the fixed transform at step zero improves
training NLL by **0.0025472083**, about **1.086%**. The 16 optimizer steps add only
**0.0000003045** of NLL improvement and **0.0000000771** of Brier improvement.
The final effective action Delta W norm is **0.00002051952**. This is a measurable
small update, not evidence of a substantial learning gain.

## Price quality remains the next model priority

Price logits were fixed across the three controls. The selected training targets
had counts **[0,4,8,12,5,3,0]**, while the model predicted
**[21,0,0,0,5,4,2]**. Its tendency to predict the first price class despite no such
targets in this subset is a concrete issue for the next diagnosis.

For comparison, the already frozen 178-row training price counts were
**[0,20,44,59,36,19,0]**. Applying Jeffreys smoothing, (count + 0.5) / 181.5,
provides a constant price distribution without fitting to these selected scores.

| Price prediction on selected 32 training rows | NLL | Brier, seven-class sum | Correct |
|---|---:|---:|---:|
| Frozen model, every control | 2.395502 | 1.000229 | 2/32 |
| Constant frozen training-pool prior | 1.502033 | 0.752655 | 12/32 |
| Uniform seven-class probability | 1.945910 | — | — |

The action intercept does not repair price prediction. Further research should
explain price marker/representation/readout behavior and why effective A/B
learning remains so small after saturation is removed. More loss-weight or
temperature sweeps on the previously examined holdout are not justified by this
result.

A future prospective experiment must load and freeze the complete new recipe,
verify loaded-output parity, and preregister evaluation against the frozen
priors. Its intercept and priors must not be refitted on validation results.
No such prospective qualification is claimed here.

## Runtime state and validation

The original resident remains at **41 completed jobs, one failed job, no running
or admitted training jobs**, with one already-trained candidate awaiting original
forward admission. The reason is `PRIOR_ORIGINAL_TRIAL_PENDING`.
The evaluation ledger contains **41 plans: 40 rejected and one pending**,
with **322 observed and 1136 censored** records across the ledger at the snapshot.

Original trial 41 retains its ending time **2026-10-18 07:23:55.417 UTC /
03:23:55.417 America/New_York**, its original window, sample plan and alpha.
This round changed none of them. The next regular-calendar FX opening in the
pinned policy is **2026-10-18 21:15 UTC / 17:15 America/New_York**; that calendar
time is not evidence that the source will be healthy.

The feed process is running, but data status is `CAPTURE_UNAVAILABLE` and
`KNOWN_OPEN_FAILURE_UNRECOVERED`. Its last valid quote remains
**2026-10-16 16:15:16.801 UTC**. The current segment at the snapshot contains zero
QUOTE and 296 FAILURE records; these are segment capture counts, not independent
market updates or lifetime totals. The current observed failure reason is
`AUTHORITATIVE_QUOTE_UNREADY`. No fresh source or successful reopening is inferred.

The final audit compared every originally protected artifact: **41/41 plan
hashes and 82/82 candidate file hashes are unchanged**. Of 29 service snapshots,
28 were unchanged, including the trainer and the critical execution, gateway,
EUR/BTC drivers, feed and Laya evaluator. The remaining current100k consumer is
a oneshot service: its state/PID changed as it exited and ran again. It was
observed, not controlled by this work. No service was signalled or restarted in
this research round.

The trainer/guard configuration hashes stayed unchanged. Manual hold was absent
at the audit and the original heavy-worker lease was idle. All three research
units completed with exit 0 and were inactive with no remaining cgroup. No model
promotion or broker orders occurred.

Validation completed on Pocket4:

- Eight focused readout helper tests passed, including decomposition, finite
  values, normalization feature preservation, unclipped saturated NLL, the
  training-only prior solve and symmetric-offset gradient behavior.
- Seventeen tests in the live-forward observer regression file passed after its
  signature-corruption fixture was repaired. Overwriting the first signature
  byte with 00 could leave a randomly generated valid signature unchanged when
  it already began with 00. Flipping one bit guarantees a different signature.
  The production signature verifier was not changed.
- Independent standard-library recomputation reproduced all three controls'
  before/after NLL and Brier within 1e-10, and verified the recipe's file hashes.
- The real diagnosis and both cached-control jobs used the existing resource
  admission, manual-hold rule, heavy-worker lease, Landlock and seccomp before
  Torch. Resource ceilings remained in force.
- Latest GitHub CI is tracked by the final synchronization receipt and PR checks;
  a queued check is not treated as a passing result.

The diagnosis used 29.638 seconds internally (31.325 seconds including its wrapper).
The two-arm cached control used 2.694 seconds internally, and the prior control
2.656 seconds. They applied 48 optimizer steps in total without more encoder
forwards. This demonstrates the efficiency of cached readout diagnosis; it is
not an end-to-end resident training throughput benchmark.

All experiment evidence, raw logits, trace tensors, adapters and recipes remain
preserved. This round retained 319,779,984 bytes of bounded COMGR cache: the
existing cleanup function accepts terminal `candidates/<task>` or
`evaluation/<task>`, while these immutable diagnostics use direct output
directories. No directory was renamed or cleanup selector broadened to force
deletion.

## Source and evidence

Worktree:

`/home/alex/work/heptatrader-train-action-ablation-20261017`

Evidence root:

`/home/alex/.local/state/heptatrader/operator-evidence/train-readout-diagnostics-20261018`

Source files:

- [Diagnostic helper](../../research/neuron_learning/laya_cell/resident_readout_diagnostics.py)
- [Focused helper tests](../../research/neuron_learning/laya_cell/tests/test_resident_readout_diagnostics.py)
- [Instrumented diagnosis](../../scripts/research_laya_readout_diagnostics.py)
- [Cached readout control](../../scripts/research_laya_readout_control.py)
- [Observer regression tests](../../tests/python/test_train_v2_live_forward_observer.py)

| Purpose | Source commit |
|---|---|
| Guaranteed signature bit flip | `75a6679e2e623d4acb5586f09a5b51f0abd91b3f` |
| Instrumented diagnosis | `119b7d93a2ef361108a4556d5cb5014ec63be550` |
| Raw / normalized controls | `136d3eeab9c4c0f05491909972660cb35aa90204` |
| Fixed training-prior intercept | `2044dcd6128ef8622e13bb7b2ce6e8c69a01be0c` |

The sealed plans bind each experiment's exact source inventory. Reproduction
requires its recorded commit and launch settings, not applying an old plan to
a later source version.

Key immutable evidence relative to the root:

| Evidence | SHA256 |
|---|---|
| `diagnosis-v1/result.json` | `c301f2e2d240f0051bdf0d325b0c7964993e3c1cd1b16dc867a34f58243ea5a4` |
| `diagnosis-v1/activation-trace.safetensors` | `a7ef5fed549c3a70843a27cd4464b0be93bd6e1d3cd9894661a0a00f7772059c` |
| `diagnosis-v1/frozen-readout.safetensors` | `cf9126b10d708697d40c309343a5f91caf8d003a1cc644a8d19a02412737ac77` |
| `control-v1/result.json` | `2284dfe2058ab5378979841c7bf4d7370304cef521ebb5c47731395a6332b2f3` |
| `prior-control-v1/result.json` | `5f354bf02b0255f05d622d5e27a203fcfdac337590803fe8ecd8d885007c6d4a` |
| `prior-control-v1/research-recipe.json` | `fc5f5eb855fa2f119576550c88d65680b61be0f511a453c81d7037632abaf680` |
| `prior-control-v1/prior-intercept-profile.json` | `8d7494349671314437168a655bc4312b3b8d051724619f729ecc412c1e908161` |
| `prior-control-v1/prior_anchored_cls-adapter.safetensors` | `012c25ce21cc442881e85f94ae1ee91f69b7f23f7bf8d5ae2661ccdc6eefe23a` |
| `final-metric-verification-r1.json` | `d5e5139f21856eeebe19a9babf51e9cf9d179a754022876bce9ab01e328be424` |
| `final-runtime-audit-r1.json` | `6cb89287212f191c323e2ef4c90e20968c2282ac64c6aa58e00cd27c415fb8dc` |

The **global** prior-control result is the complete provenance entry point: it
binds the research recipe, which binds the prior profile, adapter and frozen
readout. The per-arm result was written before the recipe hash was attached to
the global result; it should not be presented as containing the full recipe
binding by itself.

The recipe explicitly disables automatic resident selection, requires new
prospective validation and grants no trade authority. Its intercept is an extra
parameter outside the original adapter-only norm profile. Saving just A/B would
omit part of the measured transformation; the recipe must travel with it.
