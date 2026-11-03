# Future Current100k CPU standby profile v2

This is an opt-in implementation for a **new, independently registered plan**.
It does not modify a producer, actor, trading TTL, preview permit, risk rule,
existing plan, sample threshold, or the original heavy-worker Guard. It does not
provide a strict guarantee that every OPEN has an earlier prediction. That
would require a separate producer contract before command preparation.

## Finite lifecycle

The new signed `cpu_standby_profile` binds the exact module source and fixed
bounds: 30 seconds of preroll, at most 180 seconds before GPU admission, at most
150 seconds of CPU idle time, at most 240 seconds total, and one actual request.
An external finite calendar can invoke another bounded process. The worker
does not renew its own lifetime.

1. The CPU-only parent verifies the pinned runtime, original signed plan,
   candidate, producer graph and Guard configuration. Before input start minus
   30 seconds it does not acquire a lease or launch a model child.
2. The original `Gate.acquire(value-predictor)` grants the initial heavy lease.
   The child inherits this exact locked descriptor and the predictor controller
   lock. It installs the existing sandbox, selects the already accepted HIP
   runtime **before Torch import**, and loads the encoder and value head on CPU.
3. The child verifies every parameter and buffer is CPU, no gradients exist,
   `torch.cuda.is_initialized()` is false, and no `/dev/kfd` or `/dev/dri/*`
   descriptor exists. The parent independently inspects the child's descriptors.
   Failure stops the process; it never loosens `/proc` permissions.
4. A private bounded `SOCK_SEQPACKET` handshake closes the child's initial heavy
   descriptor first. The parent checks the descriptor is absent, then releases
   its own original Gate lease. CPU standby is now lease-free and GPU-free.
5. Only a real fresh flat request in the original input window can trigger a
   second `Gate.acquire`. The parent requires its old lease field to be `None`,
   rereads original evidence, and passes the new heavy descriptor with
   `SCM_RIGHTS`. Each admission records the exact request ID and SHA, original
   decision and wall/monotonic age. Denial cannot send a GPU command.
6. The child rechecks the request, candidate, maintenance state, original OPEN
   evidence, admission age and CPU-only state immediately before its first GPU
   call. It copies the prepared model/head to GPU and invokes the original
   predictor exactly once. Publication still uses the original request deadline,
   signatures and custody. No time is backdated and no missing request is made.
7. The child exits after this GPU phase. It never returns to CPU standby. Its
   fresh descriptor stays open through actual process exit; the parent reaps
   before releasing its copy. A failed reap retains the parent descriptor.
   Parent death uses the existing kernel death signal, and the inherited
   controller lock prevents a second controller overlapping an orphan child.

Malformed packets, extra/truncated descriptors, stale or negative admissions,
changed request scope, original OPEN preparation, maintenance, source failure,
clock reversal and fixed limits all drain the session. Bounded shutdown is
TERM for 10 seconds, then KILL with a 2 second reap allowance. Systemd retains
its 240 second start timeout, 20 second stop timeout and control-group kill.

An exact DONE already queued before a later OPEN/window/Guard change remains
an acknowledged completed result. Cleanup may recover a queued receipt after
reap, while preserving the original drain/error reason. `NO_VERIFIED_DONE` is
explicit; a zero acknowledgement is not proof that no publication occurred.

## Future registration and staging

The existing staging CLI accepts an explicit new option:

```text
stage_current100k_warm_flat.py <existing complete pinned staging arguments> \
  --worker-profile cpu-standby-v2
```

The default is `warm-flat-v1`. The stager only writes a new content-addressed
release, fresh role configurations, registration request and unit candidates.
It does not sign a plan, start services, acquire a model lease or import Torch.
The existing same-producer registration precheck, prior outcome-end condition,
complete trial history, source accounting and ordinary signed registrar remain
in force. The profile and source enter the new plan **before** its ID and role
signature are computed. Old signed plans cannot acquire this profile later.

The normal finite launcher chooses the v2 parent only for this exact profile.
Calling the ordinary cold `run(..., 'predict')` with a v2 config fails. General
registration and prediction pin checks require config/plan profile agreement
and the actual imported authority modules to belong to the staged runtime.
Mixing v1 and v2 profiles is rejected.

For v2, only the predictor's finite calendar begins at `input_start_ms - 30000`.
The original input, reward and evaluation windows are unchanged. First activation
must complete before that preroll boundary. A later same-plan repair reports
whether it missed the boundary; it cannot extend or reset the signed window.
The explicit v2 predictor unit requests CPUQuota 400%, CPUWeight 500, two Torch
threads and the existing 4 GiB memory limit. Other profiles keep their defaults.

No current continuation request is changed by these sources. Selecting v2 for a
future trial is a separate, reviewable stage/registration action. In particular,
it does not reset the current producer's accumulating training population.

## Validation and remaining hardware gate

Local tests exercise actual socketpair/SCM_RIGHTS descriptor passing, real flock
ownership and subprocess reap, plus CPU model/Guard/sandbox fixtures for failure
ordering. The local executor denies cross-process `/proc/PID/fd` inspection;
that one observation is explicitly mocked in process fixtures. Production keeps
the real fail-closed inspection. A fresh real flock acquisition also independently
proves the old child copy was closed in those fixtures.

Existing parent-death, v1 lifecycle, signed-registration, source-accounting and
activation tests remain applicable. These tests do **not** establish real GPU
speed, installed-library compatibility or numerical equivalence.

Before selecting v2 on Pocket4, a separate historical diagnostic must establish:

- CPU parameters and all buffers, no CUDA initialization and no GPU device FD
  after the real sandbox/HIP selection/Torch/model load.
- Both old descriptors gone, original fresh Guard admission, exact new
  descriptor ownership and exit/reap before final release.
- Exact parameter/buffer hashes and tokenizer input IDs/masks between CPU-loaded
  then transferred weights and the original direct GPU loader, followed by
  same-input serial BUY/SELL feature/value comparison.
- Actual request-to-result timings, CPU quota/throttling, RSS and GPU peaks.
  UMA RSS and GPU accounting must not be added.

The historical diagnostic creates no forward seal, plan, trial or order and
does not count as coverage for any old or future trial. Even good parity and
timings do not close the separate strict pre-OPEN causality requirement.
