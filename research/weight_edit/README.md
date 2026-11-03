# Current100k weight study

This research tool inspects and edits a frozen monetary readout. It does not submit
orders, change resident services, replace production weights, or claim prospective
performance from already inspected labels.

## Private inputs and outputs

Keep real prompts, labels, checkpoints, features and results outside Git. Prepare a
private `snapshot/` with `candidate.json`, `value-head.safetensors`,
`dataset-selected.json`, and `source-manifest.json`. The manifest's `files` maps each
filename to its SHA-256 and source path. The tool verifies those hashes, ordered row
IDs, label identity, and each feature-text hash before proceeding.

The fixed encoder is loaded through the existing verified Laya loader, with
`enforce_gpu=False`. Encoding uses the exact complete feature text, no truncation,
a 2,048-token ceiling, one row per batch, last hidden state, attention-mask mean
pooling, and FP32 L2 normalization to 768 features. The cache binds the ordered text
hashes, model and tokenizer file identities, loader/reference source hashes,
PyTorch/Transformers versions and explicit CPU backend. GPU output logs are only
used to report cross-backend discrepancies; all paired candidate scores share the
same CPU feature matrix.

Run with low scheduling priority and environment thread limits set **before Python
starts**. PyTorch's thread setting alone may not limit a third-party BLAS pool.
On a shared machine select two available CPU cores with `taskset` if needed.

```bash
env OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 NUMEXPR_NUM_THREADS=2 \
  nice -n 19 /path/to/venv/bin/python -B current100k_study.py extract \
  --study /private/study --runtime /pinned/current100k/runtime \
  --base /pinned/laya/base --threads 2

env OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
  nice -n 19 /path/to/venv/bin/python -B current100k_study.py analyze \
  --study /private/study --threads 2
```

The extractor is resumable from individually committed feature files. A completed
manifest is immutable: use a new study directory for a new extraction contract.
Only private files are written. At least 6 GiB MemAvailable is required before
loading and at least 3 GiB before a forward pass. No GPU is selected or initialized.

## Fixed comparisons

| Arm | Operation | Interpretation |
| --- | --- | --- |
| parent | unchanged head | Current frozen baseline |
| bias_shift_mean | change one bias to remove mean residual | Calibration on known labels |
| constant_mean | zero weights and training-mean bias | Analytic baseline; not the original norm-constrained candidate class |
| constant_median | zero weights and training-median bias | Analytic MAE baseline; same qualification |
| ridge_normcap_0p1 | linear least-squares fit with joint weight/bias norm at most 0.1 | Closed-form data fitting, with scalar constraint solve |
| sgd16_reference | original zero-init, 16 SGD steps at lr 0.05, Smooth L1, original bounds | Reproduction of original update count; not an equal-time control |
| sgd_head_budget_2s | zero-init SGD with two-second construction ceiling and 8,192-step cap | Equal maximum head-construction budget; actual utilization reported |
| covariance_direction_patch | mean correction plus one centered residual-covariance direction, clipped to joint norm bound | Label-dependent local fitting |
| common_direction_half_ablation | subtract half the weight component along feature mean; compensate the bias to preserve the mean | Mechanistic ablation, not new knowledge |

Each candidate is serialized as FP32 safetensors, reloaded, and evaluated through a
real `nn.Linear` forward pass. Actual changed coordinates, parameter/delta norms,
file hashes and module-forward equality are recorded. The original 0.1 joint norm
bound applies to research candidates. Constant baselines are explicitly marked
`analytic_baseline` if their bias alone exceeds it; they are not falsely presented
as eligible production candidates. Development metrics use raw outputs. A future
collector must apply its frozen output bound consistently to all arms and retain
raw values.

The four input probes (action flip, JSON reserialization control, 10-pip common
price shift, trend reversal preserving spreads) on four fixed rows measure input
sensitivity only. They do not create real outcome labels. All 54 historical labels
were viewed before this work; the tool makes no blind holdout or winner-selection
claim. Future efficacy requires a new frozen prospective window with original
request/command identity and pre-open predictions.

## Resident integration

`WarmCpuEncoder(runtime, base, threads=2, batch_size=1)` loads one CPU encoder. Call
`encode_request(request)` with the exact pre-open REQUEST dictionary. It returns
`features` and `feature_text_sha256` for BUY and SELL, `base_id`, `feature_profile`,
`backend`, receipts, and `computed_at_ms`. Share those two vectors across all small
heads; never load an encoder per candidate. The caller owns scheduling, leases,
original-request verification, durable pre-open sealing and eventual outcome joins.
This helper alone does not confer trading or production authority.

## Verification

`test_current100k_study.py` checks the SGD common-mode recurrence, an actual
serialized/reloaded single-bias forward, constrained norm behavior, malformed or
nonfinite head rejection, and immutable conflicting-result rejection. It needs
PyTorch and safetensors but no pretrained model or market data.

## Resource and loader details

Loader and relative imports use a private package rooted in the pinned runtime,
so an already imported newer `laya_cell` cannot silently redirect the loader.
The returned receipt includes the actual loader SHA-256. Optional threads=4 and
batch_size=2 jointly encode both actions under a distinct backend identity.
The actual four-request CPU benchmark took 14.23 to 22.51 seconds and did not
meet the pre-open deadline. This option is not a demonstrated latency fix.

Shared feature extraction is accounted separately from candidate construction.
The base recipe group and each extra recipe have a two-second ceiling; the SGD
loop admits steps only until 1.95 seconds with an 8,192-step cap. A 50 ms finish
allowance is checked. Actual wall/CPU utilization is retained; equal allowance
does not imply equal consumption. No transformer backpropagation occurs.
Closed-form fitting still uses labels and is not data-free skill injection.

Seven tests additionally cover private package isolation and bounded SGD/local
patch behavior. `RESULTS.md` records aggregate findings without real prompts,
row identifiers, individual labels, vectors or weights. The full evidence stays
in the private study. Future efficacy requires new original requests, pre-open
sealing, valid paired outcomes and the full evaluation horizon.

## Pinned HIP replay

benchmark_hip.py verifies original predictor-config bytes, supplied HIP helper
bytes, original runtime, model identity and original guard. It binds accepted HIP
libraries through an exec boundary before importing Torch. Successful original
resource admission is required for one encoder load. It replays four known
requests after fixed warmup and checks features and all small heads against the
CPU cache. Limits are feature max-abs 2e-5 and all-head raw USD max-abs 0.001.
Load, warmup, encode-plus-head latency, peaks and cleanup receipts are recorded.

The helper is laya_cell.current100k_hip_encoder from resident integration. It must
provide strict model/workspace cleanup. The benchmark retains the lease until
process exit if cleanup is uncertain, records failure, and changes no admission
hold. Supply --config-sha256 from actual file bytes rather than a filename. Both
benchmark and helper source hashes are recorded. Current successful measurements
are in RESULTS.md; no batch-two HIP experiment was needed after batch one passed
the bounded historical timing/parity requirement. Future efficacy remains open.
