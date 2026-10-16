# Pocket4 Laya price-head ablation and one-year historical research

## Scope

Research is strictly on Pocket4. No model promotion, trade permission,
canonical owner registration, original experiment modification, order
placement, or external device storage is involved. The existing Value
priority and original heavy-worker lock remain enforced.

## Real PAPER source: GPU price-only vs original action loss

The original frozen dataset from the 38th Laya GPU training job has
512 samples, 917 mature label rows, and 485 five-minute target rows.
Immutable SHA256:
696d31cea8bcb13f9fbfe5d94f2c0bcc22a84034fb65078455b36774afff90e5

The source only spans about 91 minutes. Hundreds of overlapping observations
are NOT hundreds of independent five-minute periods. The ablation retains
the same predecessor, frozen full Laya backbone, parent adapters, tokenizer,
GPU gfx1150, AdamW LR 0.0001 and 16 two-sample optimizer steps. It compares
the original weighted auxiliary action loss (0.01) with zero action loss.
Both models are stored as isolated immutable research safetensors, never as
eligible resident candidates.

The predetermined split from the first real anchor is training until
35 minutes; validation 45–60 minutes; test starting after 70 minutes.
All train labels must mature before the train cutoff. The 32 rows per
population contain only six, three and four nonoverlapping 5-minute
anchors in train, validation and test, respectively. Model selection uses
validation only, and all variants are benchmarked against a train-only
class-frequency prior. The final screen always refuses a production
signal without 20 independent test periods.

Actual GPU output:
~/.local/state/heptatrader/operator-evidence/laya-price-gpu-ablation-20261016-r3/

## Independent source: revised historical EURUSD 5-minute bars

The longer local source has 73,169 timestamped 5-minute bars,
2025-10-16 to 2026-10-13. Its file SHA256 is
3988a917554fc7322ae06a38560d4a9c91b5137fceccdc561830ec4327987f12.
It is explicitly flagged as revised historical data, not original
contemporaneously sealed quotes. Treat it as offline exploratory research,
never as a substitute for original event-time prediction/outcome proof.

The causal feature vector uses the preceding 12 five-minute midpoint
close returns, three trailing means, three absolute-movement means,
current-bar range, current-bar bid/ask spread, and UTC hour/weekday
cycles (24 features). It predicts the immediate next continuous
five-minute midpoint close interval using the existing Laya six bin
edges. Weekend, market-close or other gaps are excluded, not filled.
A source bar is only treated as observed after its complete bar-close,
not at the beginning of the five-minute interval.

The preexisting 14 rolling windows of 120-day train, 30-day validation,
15-day test and 900-second embargo are reused. Per fold, train-only
mean/standard deviation normalize the 24 features. The fixed family is
class-frequency prior, GPU linear softmax, and GPU 24-32-7 tanh MLP.
Training uses AdamW LR 0.003, 0.001 weight decay, batch 1,024 and at most
12 epochs with validation-only early stopping. The neural candidate
must beat or tie the prior in BOTH validation Brier and NLL before being
chosen; test labels are consulted only after selecting the family.

This does not validate financial alpha or real future performance, even
if revised-history test folds improve. Nothing in this experiment can
change the existing Laya scorer weights or authorize trading.

## Validation

Adversarial CPU tests address duplicate/out-of-order bars, malformed
and crossed prices, nonfinite numbers, exact five-minute horizon,
not leaking future bar closes into preceding features, excluded
session/weekend gaps, temporal purge, label maturity, prior smoothing,
malformed score rejection and deterministic model-family selection.
Original benchmark tests and live service hashes are rechecked before
any research branch merge.

The preexisting separate read-only 90-window baseline experiment
continues under its own immutable plan. Do not duplicate its runner,
reinterpret its source-only coverage as original neural qualification,
or modify its trial.

## Observed real GPU results (Pocket4, 2026-10-16 UTC)

The original frozen full-Laya two-arm GPU ablation has completed:
both arms applied all 16 optimizer steps to 32 training samples. Original
action weight 0.01 changed 37 auxiliary-head coordinates; the price-only
arm changed zero action-head coordinates. Their price probabilities and
validation/test scores were identical in this particular cohort.
Both test models were worse than the original train-frequency prior:

| 91-minute source holdout: only 4 disjoint 300sec anchors | Brier | NLL | Top-1 |
| --- | ---: | ---: | ---: |
| Frozen parent | 1.004421 | 2.383855 | 0/32 |
| Original-weight research arm | 1.004339 | 2.383607 | 0/32 |
| Price-only research arm | 1.004339 | 2.383607 | 0/32 |
| Train-only smoothed class prior | **0.651508** | **1.341700** | **17/32** |

A necessary fine distinction: the research code recalculates auxiliary
correctness targets from the current detached price logits on each update,
rather than precomputing them once from the frozen predecessor as the
production trainer does. The target computation can affect the auxiliary
research head, but should not be silently described as byte-for-byte
reproduction of the original auxiliary target sequence. The experiment
proves an identical limited-price-head result, not equivalence of all
auxiliary steps or downstream sample-independent statistical power.

Evidence: laya-price-gpu-ablation-20261016-r3.
Two earlier failed configuration-preflight receipts remain retained.

### One-year revised-history 14-fold GPU development

The first attempt completed fold zero but then legitimately deferred and
stopped following host memory PSI pressure and a real original Value
request, preserving laya-year-gpu-20261016-full14/result.json as FAILED.
No resource threshold was relaxed and no Value producer interrupted.

After host PSI returned below the original strict policy limits,
a second run performed **all fourteen** existing folds on the Pocket4
gfx1150, while respecting the original shared flock, live source checks
and Value priority. Run-state COMPLETED_GPU_RESEARCH_NO_PROMOTION;
run total wall time 82.02 sec. The exact source revision hash and
protocol ID are pinned in the evidence directory:

- Source SHA256: 3988a917554fc7322ae06a38560d4a9c91b5137fceccdc561830ec4327987f12
- Protocol ID: 8d099408b8b242b2bc51edabf3c2a69d9bcd52873315dede0eb421e2ff5d1193
- Test anchors (non-repeated between folds): **40,767**
- Validation-only model selected in every fold: tiny 24-32-7 MLP

| Historical rolling-test aggregate | Train-frequency prior | Validation-selected MLP |
| --- | ---: | ---: |
| Seven-class Brier (lower better) | 0.630590 | **0.592230** |
| Seven-class NLL (lower better) | 1.261557 | **1.175213** |
| Top-1 hits out of 40,767 | 22,830 | **22,837** |

In this exact retrospective experiment, all fourteen test folds improved
Brier **and** NLL over the static baseline, while overall top-1 accuracy
improved by just seven classifications. This suggests better-calibrated
probabilities, not a profitable trade signal. Fold samples are unique
five-minute anchors, but not independent proofs of live-market efficacy:
adjacent bars remain temporally correlated, and revised historical
data was assembled after the events. Prospective original-source and
economic out-of-sample qualification remain **unproven**.

Successful immutable result:
~/.local/state/heptatrader/operator-evidence/laya-year-gpu-20261016-full14-r2/result.json
Each fold has its validation-choice receipt, test receipt, and
individual research safetensors stored there. All training and evidence
remain on Pocket4; no model is promoted or registered in formal trials.
