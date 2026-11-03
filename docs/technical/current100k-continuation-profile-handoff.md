# Current100k continuation profile selection and pre-fit handoff

This contract applies to a future, explicitly selected continuation. It does
not change an existing continuation request, registered plan, producer,
historical outcome, or forward coverage rule. A code merge is not a hardware
qualification or production installation.

## Profile and source identity

`stage_current100k_new_scope_continuation.py --worker-profile` accepts
`warm-flat-v1` (default) and `cpu-standby-v2`. The choice is sealed in a new
`CONTINUATION_REQUEST.json`. An older request without the field continues to
mean v1. The controller passes the selection to the ordinary warm stager, whose
`STAGING_REQUEST.json`, role configs and `WARM_STAGE.json` bind it. Registration
binds the profile and its module before calculating and signing the new plan.
The precheck and activation paths already support either explicit profile;
the old continuation omitted the stager argument and therefore selected v1.

The original preparation source commit remains the historical identity of
`PREPARATION_REQUEST.json`. A new detached executor gets its own exact clean
commit, paths and request SHA. The consumer registration, preparation request
SHA, original cohort selection policy, full historical plan pins, expected
trial, original fit objective, Guard/Python/HIP configuration and fixed
registration cadence remain the same. In particular, a new execution source
does not allocate a different cohort or a different trial.

## Hardware prerequisites for v2

The sealed `worker_profile_evidence` object has exactly two keys:

```json
{
  "report": {"path": "/absolute/diagnostic/report.json", "sha256": "<64 lowercase hex>"},
  "lifecycle": {"path": "/absolute/diagnostic/lifecycle.json", "sha256": "<64 lowercase hex>"}
}
```

Both ordinary owned files are checked on staging, installation and every
continuation execution. A standalone `future-tokenizer-result.json`, partial
staged result, child report without completed parent lifecycle, or a summary
claim cannot satisfy the gate.

The report must identify the exact prior plan SHA, candidate, base and head,
with state `HISTORICAL_PARITY_EXACT` and scope
`HISTORICAL_CPU_STANDBY_PARITY_NOT_REGISTERED_FORWARD_PROFILE`.
`exact_parity` and `historical_loader_exact_parity` must be true. It must show
all CPU model parameters and buffers on CPU, no initialized CUDA, and no open
GPU device descriptors. Initial and post-inference parameter/buffer checks,
token IDs/masks, features and values must be exact, with zero maximum feature
and value differences.

The report's `future_tokenizer_parity` must additionally show all of:

- `requested`, `exact`, `tokenizer_input_ids_and_masks_exact`,
  `features_bitwise_equal`, `values_bitwise_equal` are true;
- `private_module_only_no_laya_module_replaced` and
  `same_original_gpu_model_and_head` are true;
- both maximum differences are zero;
- `source.sha256` equals the new executor's
  `research/neuron_learning/laya_cell/tokenizer.py` SHA. Its original diagnostic
  path may differ; the implementation bytes may not.

The parent lifecycle must have `phase=COMPLETE`, `error=null`,
`child_reaped=true`, `initial_lease_released=true`,
`fresh_admission_granted=true`, `fresh_lease_released_after_reap=true`, and
`parent_imported_torch=false`. Its `completion_receipt.report_sha256` must
equal the sealed report SHA and `completion_receipt.exact_parity` must be true.
Both receipts must state zero broker orders and no forward seal. The report
also must state no optimizer use, publish path or v2 plan registration.

Both receipts must use `comparison_mode=separate-process`. The report must
state `model_processes_never_overlap=true` and
`first_model_memory_released_by=PROCESS_EXIT_AND_REAP`. The lifecycle must
additionally show both staged-child and direct-child reap, the staged GPU lease
released after its reap, a fresh original admission for the direct baseline,
the direct child started and its lease released after reap. Both children must
finish with integer zero exit status in both receipts. A completed numerical
result followed by a timeout, signal termination or nonzero exit cannot pass.
Residual HIP workspace is recorded; it need not reach zero while its process
is still alive. The completion kind must be `PARENT_TWO_PROCESS_COMPARISON`,
and the staged completion receipt must pin the same four staged result files
as the final report, including the future-tokenizer comparison.

These are prerequisites for selecting the new implementation. They compare a
historical candidate and base. They do **not** establish the behavior or timing
of the future head trained from the first 32 new-producer rows.

## Handoff from the existing v1 timer

The new request may include `supersedes_continuation` with the old request's
absolute path and SHA. Only one original v1 predecessor is supported. Its
source must still be at the clean commit named by its request. Its controller,
fit, forward and source directories are retained; all four new paths must be
separate. Contract fields unrelated to the implementation selection must match.

`--install` then performs these bounded steps:

1. Acquire the old controller's nonblocking `continuation.lock`, followed by
   the original preparation's nonblocking `preparation.lock`. A busy lock is a
   refusal before timer mutation; no running service is killed.
2. Require that the old controller has only its request, mutex and status
   files, and that no old fit or forward output exists. Any fit-stage,
   fit-attempt, unknown artifact or output prevents this pre-fit handoff.
3. Check the old exact unit bytes and effective loaded executor, including
   fragment, drop-ins, argv, working directory and environment. Snapshot the
   original preparation SHA and any existing frozen dataset/ready receipt SHA.
4. Write the new immutable `PREDECESSOR_HANDOFF_INTENT.json`. Disable only the
   request-derived old timer. Wait at most 15 seconds for any old service that
   had not acquired its mutex to exit normally. Never stop or kill the service.
5. Install the new request-derived service/timer and verify actual loaded
   bytes, argv, working directory, environment, plus timer enabled, active,
   waiting and a finite next event. Recheck the original preparation/cohort.
6. Atomically publish `PREDECESSOR_HANDOFF.json` under the new controller.

Each normal new controller execution requires the handoff receipt, rechecks
the original pins, preserved cohort files and retired old timer, and retains
the old mutex for the whole new step. Thus accidental re-enabling of the old
timer does not allow two fits to proceed concurrently. The existing preparation
mutex still protects initial cohort selection when the count reaches 32.

An installation interrupted after timer retirement may be retried with the
**same** new request and SHA. Verified identical unit files and the same intent
are reused. Before the final handoff receipt exists, the new timer refuses to
train. If there is a conflicting artifact, changed pin, unknown old fit effect
or a service that does not exit normally, the installer holds for inspection;
it does not allocate another cohort, rerun a fit or silently restore a timer.
Do not delete fit artifacts or use a different output root to bypass that hold.

## Pocket4 command preparation

Run as the original unprivileged operator, after integrating this patch and
obtaining the completed hardware receipts. Four values below deliberately
remain required operator inputs: the reviewed integration commit, diagnostic
directory, report SHA and lifecycle SHA. They must come from actual results.
The command stages only; it does not retire the old timer.

```python
import hashlib
import json
from pathlib import Path
import re
import subprocess

repo = Path('/home/alex/work/heptatrader-next-20261102')
expected_head = '<reviewed integrated commit>'
diagnostic = Path('<absolute completed diagnostic directory>')
report_sha = '<actual report SHA256>'
lifecycle_sha = '<actual lifecycle SHA256>'
assert re.fullmatch('[0-9a-f]{40}', expected_head)
assert diagnostic.is_absolute()
assert all(re.fullmatch('[0-9a-f]{64}', value) for value in (report_sha, lifecycle_sha))

old_path = Path('/home/alex/.local/state/heptatrader/operator-evidence/fullpush-20261102/'
                'current100k-newscope-continuation-v1/CONTINUATION_REQUEST.json')
old_sha = '8d940acba3e009591f48d1d8ef7584725446ffd47e1f64fd1bed9ffe30881f11'
raw = old_path.read_bytes()
assert hashlib.sha256(raw).hexdigest() == old_sha
old = json.loads(raw)
command = ['/usr/bin/python3', '-B', str(repo/'scripts/stage_current100k_new_scope_continuation.py'),
    '--repo', str(repo), '--expected-head', expected_head,
    '--source-directory', '/home/alex/work/heptatrader-current100k-continuation-v2',
    '--controller-root', '/home/alex/.local/state/heptatrader/operator-evidence/fullpush-20261102/'
                         'current100k-newscope-continuation-v2',
    '--fit-output-root', '/home/alex/.local/state/heptatrader/neuron-learning/'
                         'current100k-newscope-value-fit-v2-20261102-3c6d7f252a3e',
    '--forward-root', '/home/alex/.local/state/heptatrader/neuron-learning/'
                      'current100k-forward-trial5-cpu-standby-v2-20261102-3c6d7f252a3e',
    '--worker-profile', 'cpu-standby-v2',
    '--worker-profile-evidence-report', str(diagnostic/'report.json'),
    '--worker-profile-evidence-report-sha256', report_sha,
    '--worker-profile-evidence-lifecycle', str(diagnostic/'lifecycle.json'),
    '--worker-profile-evidence-lifecycle-sha256', lifecycle_sha,
    '--supersedes-request', str(old_path), '--supersedes-request-sha256', old_sha]
for key in ('preparation_request', 'preparation_request_sha256', 'fit_template', 'fit_template_sha256',
            'prior_forward_stage', 'prior_forward_stage_sha256', 'guard_config', 'guard_config_sha256',
            'fit_timeout_seconds', 'activation_readmission_seconds', 'activation_events'):
    command += ['--'+key.replace('_', '-'), str(old[key])]
subprocess.run(command, check=True)
```

Review the staging receipt's `request`, `request_sha256`, original preparation
unchanged flag, worker profile, CPU-only check, and both hardware pins. Execute
the receipt's exact `explicit_argv` with `--install` appended to perform the
locked timer handoff. That command uses the detached executor and rechecks all
inputs. Preserve both new receipts and read the actual units and new status.
The 61-second registration wake, 120 events and original fit timeout are copied
from the existing request, not recalculated from wall time.

## Acceptance and remaining evidence

An installed continuation means a verified persistent controller is waiting
for the original cohort or progressing through the original guarded fit,
normal `NEW_PRODUCER_SCOPE` registration and native activation. A registered
plan and enabled role timers are another distinct stage. Neither proves P0.

`CONTINUATION_COMPLETE.json` therefore retains:

- `current100k_p0_closed=false`;
- `same_request_preopen_prediction_chain_verified=false`;
- for v2, `cpu_standby_observed_before_first_request=null` and
  `first_flat_request_still_requires_cold_model_start=null`;
- the requirement for matching candidate, producer and request identities with
  `request_start <= computed <= committed < min(original OPEN, first fill)`.

The observer must supply the empirical same-request chain for real future
commands. Guard/source denials, fresh-admission delays and requests that arrive
before CPU standby remain observable possible misses. This profile introduces
no driver-side wait-for-seal contract and makes no guarantee that every OPEN
will have a prediction. The producer and its 32-row bootstrap requirement are
unchanged.

## Targeted validation

From the selected source checkout:

```bash
PYTHONPATH="$PWD/scripts:$PWD/research:$PWD/research/neuron_learning:$PWD/learn:$PWD" \
python3 -B -m unittest \
  tests.python.test_current100k_continuation_profile \
  tests.python.test_current100k_new_scope_continuation \
  research.historical_training.tests.test_current100k_cpu_standby
```

These tests use isolated temporary files and mocked service inspection. They
cover incomplete parent lifecycle, changed tokenizer, profile routing,
concurrent mutex holders, unknown fit effects, native unit mismatch,
interrupted same-request install, actual next-event requirements, and retained
P0 limitations. They do not replace hardware or future real-request evidence.
