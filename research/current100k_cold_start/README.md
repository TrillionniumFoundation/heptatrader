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
  Gate for a later real request. The opt-in implementation and its separate
  historical hardware diagnostic are described below.
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

## CPU standby and numerical parity diagnostic

The future signed worker is described in [CPU_STANDBY_V2.md](CPU_STANDBY_V2.md).
Before selecting it, run the separate historical diagnostic as the ordinary
runtime owner under the intended CPU quota/weight. Retain the original
configuration and reuse the exact request ID/SHA from `serial-02/input-proof.json`:

```bash
/usr/bin/python3 -B /absolute/path/cpu_standby_diagnostic.py \
  --config /absolute/path/original-predictor-CONFIG_SHA256.json \
  --sha256 CONFIG_SHA256 \
  --request-id SERIAL02_REQUEST_ID --request-sha256 SERIAL02_REQUEST_SHA256 \
  --threads 2 --timeout-seconds 120 \
  --output /absolute/path/new-cpu-standby-parity-01
```

Keep `benchmark.py` adjacent to the diagnostic. `input-proof.json` records both
diagnostic source hashes plus the immutable runtime/config/plan/head/request
identity. The actual selected release's loader must explicitly expose
`enforce_gpu`; preflight checks this before original Guard admission. Legacy
258 head behavior uses the already verified exact source-hash compatibility
path, including its original unclamped USD conversion.

The diagnostic uses the signed historical plan only as input provenance. It
does **not** opt that plan into v2. After the first original admission, the child
installs the original sandbox, proves the original role key is unreadable,
selects accepted HIP before Torch, and loads actual CPU weights/head. It hashes
all parameters and registered buffers, including nonpersistent buffers, and
the serial BUY/SELL tokenizer input IDs/masks. Both processes then close their
initial heavy descriptors. The parent independently verifies the child's FD
table, waits one fixed second with CPU state resident, revalidates the original
inputs, and reacquires the original Gate. Only a newly transferred locked FD
permits GPU setup and inference.

It first measures CPU-to-GPU copy and actual serial inference. Then, while
holding that fresh lease, it destroys the first model and requires zero Torch
GPU allocation before loading the original direct-GPU model. It compares all
initial parameter/buffer hashes, tokenizer tensors, post-inference state,
features and USD values. No relaxed tolerance turns differences into a pass.
An exact result exits 0; original Guard denial exits 3; numerical differences
exit 4 and remain explicit in the report. Other errors preserve partial receipts
and exit with their actual failure. No model survives the finite child.

Read `cpu-ready.json`, `cpu-idle.json`, `fresh-admission.json`, `report.json` and
`lifecycle.json` together. In particular, require CPU-only/no-GPU-FD proof, both
initial copies closed, a fresh original positive admission, the exact parity
fields, and actual reap before final lease release. `parity-state-hashes.json`
contains the complete per-tensor comparison evidence; no bearer or signing key
is captured.

`fresh_admission_to_staged_result` includes the fresh original source recheck,
descriptor delivery, first GPU setup, copy and serial prediction. CPU preload
and CPU hash instrumentation occurred earlier. Publication/signing/fsync is not
measured. The direct loader runs second in the same process with uncontrolled
OS/compiler caches; its time is not another process-cold benchmark. The report
identifies the exact historical loader/head version tested. A future change to
either still needs its own parity evidence; this does not certify unexecuted
code paths or a different centered head.

The first successful GPU result is also saved immediately in
`staged-result.json`, with CPU features/values, timing and resource evidence.
`staged-state-hashes.json` preserves the CPU initial and post-inference state.
These files survive a later comparison or cleanup failure and remain explicitly
historical diagnostic results, with no forward seal.

After first-model destruction the diagnostic writes
`allocation-after-destroy.json` **before** enforcing zero allocated GPU bytes.
It records allocated/reserved bytes, Torch allocator statistics, bounded segment
metadata and a bounded list of live GPU tensors (shape, dtype, storage size,
hashed storage identity and referrer types/key names). It never captures tensor
contents in that inspection, calls a global compiler/runtime reset or lowers the
zero requirement. Nonzero allocation prevents the second model load; actual
process exit/reap still precedes final heavy-lease release. This distinguishes
Python references from possible native/context-lifetime allocation without
pretending a per-process cache must disappear when one model is deleted.

To also exercise the future standalone tokenizer adapter, add the exact source
file and SHA:

```text
--comparison-tokenizer-source /absolute/candidate/source/tokenizer.py \
--comparison-tokenizer-sha256 EXACT_FILE_SHA256
```

The parent checks its source/API without importing ML. The child allows that
one file read-only in the original sandbox, verifies it again, and compiles the
verified bytes into a private diagnostic module (no sibling bytecode cache and
no replacement of `laya_cell` modules). While the staged GPU model/head is still
alive, it compares the same request's tokens/masks, features and values against
the CPU-loaded ordinary AutoTokenizer. `future-tokenizer-result.json` is written
before model cleanup and `future_tokenizer_parity` is a separate final-report
field. Runtime versions and tokenizer class are recorded. The adapter's source
is invoked as supplied; a conditional fallback inside it is not mislabeled as
proof that its optimized branch ran. The extra tokenizer load/inference is
separate from the first staged prediction's timing.

Diagnostic RSS/GPU peaks include verification hashing, optional comparison and
any second load. They are not the production worker's peak working set. A later
two-process comparison can avoid requiring context-lifetime allocations to clear
in one interpreter, but each process must receive its own newly admitted
original lease and the first must be reaped before admitting the second.
