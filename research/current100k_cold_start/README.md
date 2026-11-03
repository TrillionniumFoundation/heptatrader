# Current100k cold-start diagnostic

This directory contains an **offline timing diagnostic**, not a forward worker
or a new runtime profile. It uses a real, already-recorded flat request from the
selected producer graph. It does not create requests, forward seals, plans,
trials, orders, or model updates. A historical replay never counts as timely
coverage in the original trial.

## Run on Pocket4

Run as the ordinary runtime owner. Supply the existing predictor configuration
and its actual SHA256. The configuration selects the frozen release modules,
original Guard, accepted HIP runtime, Laya weights, signed plan and value head.
Use a **new** diagnostic output directory outside the forward root. Run under
the intended CPU resource profile; the script reports the actual cgroup quota,
weight, throttle counters and memory state.

```bash
/usr/bin/python3 -B /absolute/path/benchmark.py \
  --config /absolute/path/predictor-CONFIG_SHA256.json \
  --sha256 CONFIG_SHA256 \
  --request-id latest-flat \
  --threads 2 --repeats 3 --inference-mode serial --timeout-seconds 90 \
  --output /absolute/path/new-diagnostic-directory
```

`latest-flat` selects the latest real flat request whose original timestamp
precedes benchmark start. To repeat an identical input, pass its reported
`request_id` plus `--request-sha256 REQUEST_SHA256`. No response or future reward
enters the feature construction. Original source prefix and record SHA remain
in the output, and raw input custody remains private in `input-proof.json`.

The parent calls the existing `validate_runtime`, full signed registration
validation, original verified ingress, `verify_config`, and `Gate.acquire`.
Before admission it also parses the selected release's model API without
importing Torch. The historical `258...` release is accepted only with its
reviewed exact value/predictor source hashes: it constructs `FinancialHead`,
strictly restores tensors, checks finite tensors and the original 0.100001 norm
bound, and computes `head(features) * 100` without new clipping. Later releases
use their own `load_value_head` / `prediction_usd` helpers. Missing or ambiguous
APIs fail before model admission; the benchmark never patches a frozen release.
Guard denial exits 3 without a model child. After admission the child inherits
the same locked descriptor, installs the original Landlock/seccomp sandbox,
selects the accepted HIP before importing Torch, and runs the exact existing
`load_verified_laya`, serial BUY/SELL `features_from_laya`, original value head,
and `prediction_usd`. The parent waits/reaps before releasing its descriptor;
the child retains its copy through process exit and has a parent-death guard.
GPU memory uses the original 4 GiB per-process ceiling. No role signing key is
added to the sandbox allowlist, and the diagnostic has no signing call.

## Read the result

`report.json` separates original CPU validation and admission, Torch/GPU setup,
model loading, head/feature preparation, and each inference. Iteration 0 is the
first inference in a new process; later iterations retain the model/tokenizer.
`cold_model_head_first_inference_ms` excludes Torch setup and CPU front work;
`rounds[0].parent_start_to_result_ms` includes those costs and the diagnostic
child launch. These two numbers answer different questions.

This measures a process-cold run with **uncontrolled existing OS page and GPU
compiler caches**. It neither flushes those caches nor labels the result as a
storage-cold run. Reported process RSS and GPU bytes share UMA and must not be
added. The publication/signing/fsync path is not exercised, so even a result
below a historical request-to-OPEN gap is only timing evidence, not proof of
future pre-OPEN seals. One request also does not establish a worst-case bound.

The default remains serial, as in current production. Optional `--inference-mode
batch` is a separate experiment; compare the same request SHA, per-action token
counts, numerical feature/value differences and actual memory peaks before
proposing a new signed inference runtime. Existing training code explicitly
uses serial encoding to avoid a prior full-batch UMA spike.

## Source conclusions before changing a runtime

* `current100k_forward_worker.run` returns before input start and before any
  fresh real flat request. `current100k_warm_flat` v1 additionally requires a
  child READY message with `model_loaded=False`; it does not prewarm the first
  request. Its parent drains at the first original OPEN preparation.
* The original `Gate.check(value-predictor)` checks memory, PSI and fresh original
  source evidence; it does not require a request. A future **new signed** CPU
  staging profile can therefore acquire the original Gate before the input
  window, prepare CPU state, prove all model/head tensors are CPU and no GPU
  runtime/device ownership exists, release the lease, and reacquire the original
  Gate for a later real request. This lifecycle is not implemented here.
* `resident_hip_runtime.activate` must precede the first Torch import. A CPU
  staging child cannot import Torch and then activate HIP on demand. It must
  select HIP first without initializing a GPU, and must pass a fresh inherited
  lock to any later GPU phase. Merely closing the parent's descriptor while a
  child retains the old descriptor does not release the flock.
* The two actions have different text in a bidirectional encoder. Their feature
  vectors cannot be collapsed to one shared encoding. Batch size 2 is a possible
  implementation experiment, not a proved speedup or a memory-neutral change.
* Gateway `trade.place_order`/`risk.preview_order` schemas have no original model
  request SHA or prediction-seal field. Its execution decision lease binds
  domain/account/instrument/owner, not a prediction. The original preview permit
  lasts at most **5 seconds**, is consumed once, and binds the exact command.
  Waiting after prepare would both consume this short budget and cause warm v1
  to drain. Strictly requiring a seal before every OPEN needs coordination
  **before preparation**, retained original request/decision identity, original
  signal TTL, and a fresh original risk preview. That changes producer behavior;
  it cannot honestly preserve the old producer manifest.

Local boundary validation:

```bash
python -B -m unittest discover -s research/current100k_cold_start -p 'test_*.py' -v
```

These tests use no Torch or GPU. The actual loader, sandbox and HIP benchmark
must be exercised on Pocket4; local mocks are not a claim of GPU success.
