# Current100k prospective weight comparison

This separate value-only observer does not modify original trials, issue orders,
adopt a head, or produce production role signatures. Rows used for diagnosis or
fitting remain development data; their input, reward and row IDs are excluded.

## Register and collect

`laya_cell.current100k_weight_shadow.freeze(config, root)` freezes head hashes,
the Laya base, features, producer, period, dataset, encoder settings and baseline.
It starts five minutes after actual registration, with the original 14-day input
window and one-day closure grace. Training and evaluation producer hashes are
separate. Cross-producer comparison is explicit and does not confer same-producer
qualification. Official adoption still requires the existing NEW_PRODUCER_SCOPE
route. Original trial budgets are not reset or consumed.

Complete config shape (placeholders must be replaced with measured private data):

```json
{
  "schema": "heptatrader.current100k-weight-shadow-config.v1",
  "base_id": "<base SHA256>", "behavior_model_id": "<actual behavior model>",
  "period_sha256": "<period SHA256>",
  "producer_registration": {"manifest": "/private/active/manifest.json", "sha256": "<active producer SHA256>"},
  "heads": {
    "parent": {"path": "/private/parent/value-head.safetensors", "sha256": "<head SHA256>", "construction_kind": "FROZEN_PARENT"},
    "localpatch": {"path": "/private/patch/value-head.safetensors", "sha256": "<head SHA256>", "construction_kind": "DIRECT_WEIGHT_EDIT"}
  },
  "parent_arm": "parent", "encoder_backend": "<adapter backend>",
  "encoder_threads": 2, "encoder_batch_size": 1,
  "head_execution_backend": "TORCH_CPU_FP32",
  "hip_runtime_manifest_sha256": null,
  "training_dataset_path": "/private/training/dataset.json",
  "training_dataset_sha256": "<dataset SHA256>",
  "training_producer_manifest_sha256": "<training producer SHA256>",
  "training_mean_usd": 0.0,
  "training_input_ids": ["<all training input hashes>"],
  "training_reward_ids": ["<all training reward hashes>"],
  "training_row_ids": ["<all training row hashes>"],
  "training_cutoff_ms": 1, "labels_observed_at_ms": 2,
  "reward_config_path": "/private/replay.json", "reward_config_sha256": "<config SHA256>",
  "trade_authority": false, "automatic_selection": false, "actor_update": false
}
```

The exact training mean and complete ID arrays come from the frozen dataset.
The reward config must retain training and current producer registrations so
training labels can still be revalidated. Real prompts, features, orders,
outcomes and filled configs stay private and are never committed to GitHub.

The resident collector owns one encoder. `HeadEnsemble(plan)` loads only small
FP32 heads; `predict(features_by_side)` applies all heads to the same BUY/SELL
features in one linear operation. Features are Current100k 100,000 USD-context,
mask-mean pooled, L2-normalized 768-vectors. Outputs are multiplied by 100 and
clipped to +/-15 USD; raw values remain. A constant baseline may exceed a trained
head's parameter norm. Zero and raw frozen training mean are separate benchmarks.

Execution devices are fixed before collection. Encoder backend names use the
`LAYA_CPU_FP32_` or `LAYA_HIP_FP32_` prefix; unknown devices fail registration.
`head_execution_backend` defaults to `TORCH_CPU_FP32`. The numeric profile is
derived from both fields, rather than labeling HIP-derived features as CPU
encoder output. Every arm uses the same frozen head execution backend.

| Encoder | Head arithmetic | Frozen numeric profile |
| --- | --- | --- |
| CPU FP32 | CPU FP32 | `TORCH_CPU_FP32_SHARED_LINEAR_USD100_CLIP15` |
| HIP FP32 | CPU FP32 | `LAYA_HIP_FP32_ENCODER_TORCH_CPU_FP32_SHARED_LINEAR_USD100_CLIP15` |
| HIP FP32 | HIP FP32 | `TORCH_HIP_FP32_SHARED_LINEAR_USD100_CLIP15` |

The mixed profile transfers each shared feature vector to CPU once; it does not
run another encoder. Explicit HIP head arithmetic requires a working ROCm torch
runtime. Benchmark measurements must pin the exact backend, threads and batch
size used by the collector; a development CPU cache is not a HIP timing result.
HIP encoders require the accepted runtime manifest SHA256 in the plan and each
feature document, plus the existing `resident_hip_runtime` BEFORE_PUBLISH proof
for target `predict`. The store validates that proof both when sealing and when
reading a publication. CPU plans require no HIP selection or HIP proof. This
proof validates runtime-library mapping; it does not grant worker admission.

`ShadowStore(root).seal(request_item, feature_document, predictions)` accepts
schema `heptatrader.current100k-shadow-features.v1`, request ID/hash, producer hash,
period hash, base ID, input start, actual feature-completion time, backend,
encoder_threads, encoder_batch_size, `feature_profile=MASKED_MEAN_L2_NORMALIZED_FP32_768`,
`feature_text_sha256={BUY:...,SELL:...}` and `features={BUY:[...],SELL:[...]}`.

All arms are sealed together. Features, predictions, commit and containing
directory are fsynced; a publication marker is then written and fsynced. A
separate observation records the actual clock after that publication completed.
`seal_durable_completed_at_ms` must precede original PREPARED and first fill.
Returns include publication/observation paths and hashes. Missing publication
or observation after a crash remains unknown: no old timestamp is reconstructed.
These are local research receipts, not independent signed production witnesses
or protection against a compromised host clock.

## Current outcomes and evidence

The evaluator reuses verified attribution, native monetary replay and
`current100k.to_row` to recompute exact input, action, executions, quantity,
fees, settlement and net USD. Original commands remain in the denominator when
attribution disappears. Missing/late predictions, unsettled fees, unclosed
exposure and revoked rewards are not assigned invented zero targets. Ledger
corrections invalidate current scores; older evidence revisions remain.

Scalar metrics are USD MAE and RMSE, never multiclass Brier. Large losses remain
in the population. Support preserves 20 observed trades, three UTC days, five
trades in each time half, 95% coverage, no unresolved final commands, two target
signs, a $0.10 MAE gain, positive gains in both halves and fee stresses 1/1.5/2.
All head/reference pairs use family alpha divided by the comparison count.
The original Huber-loss Hoeffding mixture, lambda grid and predictable Lipschitz
bound provide the inferential gate. Its null is conditional no-advantage for
adapted paired differences, not IID trades. This does not guarantee arbitrary
regime shifts are harmless. Alpha is specific to this frozen research family,
not a production trial allocation.

Exploratory 95% bootstrap intervals resample entire UTC-day blocks. Cross-day
episodes merge affected blocks. Many same-day trades do not become independent
replications. The intervals are not multiplicity-corrected and are never an
acceptance gate; corrected mixture evidence is separate. Intervals appear only
after the fixed deadline. Earlier results remain PENDING. Final states are
FINAL_INCOMPLETE or FINAL_RESEARCH_COMPARISON, never automatic selection.

## Run and reproduce

Set PYTHONPATH to the pinned release's research, research/neuron_learning and
scripts directories. Register once, then run an independent CPU-only evaluator:

```sh
python -m laya_cell.current100k_weight_shadow_eval --root /private/study --register /private/config.json
python -m laya_cell.current100k_weight_shadow_eval --root /private/study --once
python -m laya_cell.current100k_weight_shadow_eval --root /private/study
```

The evaluator never loads Laya or a GPU. It skips unchanged replay and periodically
revalidates evidence. Its lock and outputs are separate from original trials.
The request collector calls the HeadEnsemble and ShadowStore APIs.

Tests use synthetic fixtures but exercise real source-prefix validation and
economic recomputation, temporal ordering, orphan publications, feature identity,
missing fees, development exclusions and paired final reports:

```sh
PYTHONPATH=research:research/neuron_learning:scripts python -m unittest discover -s tests/python -p test_current100k_weight_shadow.py -v
```
