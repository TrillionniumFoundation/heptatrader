# Explicit trainer-only producer registration for a new Current100k scope

This profile applies to a new independent v2 continuation. It does not alter the
retired 2e65 request, its preparation, any old producer data, or registered trial
rules. It does not activate a producer or select a Laya service. Integrate this
change into the clean source that already supports the PRICE gate producer's
six-file runtime. Do not deploy the older development baseline as that executor.

## Explicit binding

Seal a separate immutable JSON file, using the actual original role snapshots
and the reviewed trainer append stage. Its entire bytes receive a SHA256 pin.
The production inputs below were independently read on 2026-11-02; verify their
exact files again at the operator boundary rather than silently selecting a
later configuration.

```json
{
  "schema": "heptatrader.current100k-background-guards.v1",
  "profile": "APPEND_ONLY_TRAINER_PRODUCER_V1",
  "producer_registration": {
    "manifest": "/home/alex/.local/share/heptatrader/decision-attribution/releases/8c6b6ab0f719b279baa128f4c9006e10aa410766-prepared-7447c035fba194ba93fde3623bdc3aa79e2092d29ddd56d49e8f660f0746b313/EUR.USD/manifest.json",
    "sha256": "7cd27ee2919f893020d4dea9db90484cb7a49859e9a67588d4975ed529d98113"
  },
  "prior_trainer": {
    "wrapper": "/home/alex/.local/share/heptatrader/laya-guard/releases/d0cd7ea3c18b9eaef9c28559a812785830c042f0fa829b7b29db67895890d9e1/trainer.json",
    "wrapper_sha256": "7ecd7c2e7d1bb8e651b3139b2bfb83405675bbd64bd57fed1b3ae7155713b9a8",
    "guard_import_root": "/home/alex/.local/share/heptatrader/laya-guard/releases/d0cd7ea3c18b9eaef9c28559a812785830c042f0fa829b7b29db67895890d9e1"
  },
  "roles": {
    "trainer": {
      "wrapper": "/home/alex/.config/heptatrader-laya-resident/279493d0ea9c4b0a2244c20c85b8743daaf4a82c77284b93db3ee34716f1a9e0.json",
      "wrapper_sha256": "b6760d5a84266efc7e70e174a4b87139763fc59d824f6b42839cfb172b5334e0",
      "guard_import_root": "/home/alex/.local/share/heptatrader/laya-guard/releases/d0cd7ea3c18b9eaef9c28559a812785830c042f0fa829b7b29db67895890d9e1"
    },
    "evaluator": {
      "wrapper": "/home/alex/.local/share/heptatrader/laya-guard/releases/d0cd7ea3c18b9eaef9c28559a812785830c042f0fa829b7b29db67895890d9e1/evaluator.json",
      "wrapper_sha256": "7ecd7c2e7d1bb8e651b3139b2bfb83405675bbd64bd57fed1b3ae7155713b9a8",
      "guard_import_root": "/home/alex/.local/share/heptatrader/laya-guard/releases/d0cd7ea3c18b9eaef9c28559a812785830c042f0fa829b7b29db67895890d9e1"
    }
  }
}
```

The old wrappers have distinct paths, even though this actual snapshot has the
same SHA for both. The new trainer wrapper is under `.config`; its import root
remains the existing d0 guard release. Deriving an import root from the new
wrapper's parent would select a nonexistent or unrelated package.

`current100k_background_guards.load_binding(reference, registration,
primary_guard_path, primary_guard_sha)` performs read-only validation. The
primary guard must be the new trainer wrapper. Each wrapper and its original
configuration is separately pinned and validated by the original guard. The
prior trainer/evaluator owner data must be equal. All wrapper policy fields
except their separately pinned original-config references must remain equal.
The new trainer owner must differ only by one appended EUR.USD registration
matching this preparation's producer. BTC.USD, prior EUR registrations and
their order, model/HIP/runtime, directories, resources, holds and priority
plan remain unchanged. The actual guard files must match under the explicit
import root. This profile does not generalize to an arbitrary later append.

## Selection and continuation sequence

1. Complete normal producer activation and the separate consumer scope install.
   The exporter selects only that producer; it does not require a general Laya
   registration change to collect its own new rows. Keep all old scope data.
2. Seal a fresh `FIRST_32_CHRONOLOGICAL_CLOSED_NEW_PRODUCER_V1`
   preparation through `prepare_current100k_new_scope.py --once`, with all actual
   historical plans. Inspect its status, not only the process return code.
   `WAITING_NEW_PRODUCER_32_SETTLED_LABELS` is a valid pre-fit state.
3. Independently perform the reviewed trainer-only config selection: original
   guard SIGUSR1 drain to natural worker completion/reap, select the new
   immutable wrapper with the original frozen Python/HIP/import environment,
   then verify the actual new trainer PID/argv/config. Do not change the
   evaluator for this append. This module provides no service-selection CLI.
4. Seal the binding above and use the new clean integrated continuation source.
   Add the following flags to the ordinary independent v2 staging command:

```sh
--guard-config /home/alex/.config/heptatrader-laya-resident/279493d0ea9c4b0a2244c20c85b8743daaf4a82c77284b93db3ee34716f1a9e0.json \
--guard-config-sha256 b6760d5a84266efc7e70e174a4b87139763fc59d824f6b42839cfb172b5334e0 \
--background-guards ABSOLUTE_SEALED_BINDING_JSON \
--background-guards-sha256 EXACT_BINDING_BYTES_SHA256 \
--worker-profile cpu-standby-v2 \
--install
```

Use the new preparation path/SHA, new detached executor/controller/fit/forward
paths, the original fixed fit template/prior-stage pins and both accepted
parity03 report/lifecycle pins. The hardware validator runs against this new
source. **Do not pass `--supersedes-request`**: the old v1-to-v2 handoff contract
cannot authorize changing producer or preparation, and cannot supersede 2e65.

`--check-only` verifies the immutable configuration inputs without training or
native selection. `--install` additionally calls `verify_loaded_roles` before
any new controller-unit effect. It checks both actual loaded units, actual
PID/start identity, argv, selected environment, InvocationID, cgroup and exact
guard import paths. It refuses if trainer still selects the original wrapper.
It revalidates the pinned configuration and guard files after reading roles.

The install output includes `background_runtime_verification`. Read back the
new request-derived timer and the first real controller status. The initial
wrapper check is not imposed on every timer tick: a later normal priority
handoff must be able to resume its original/selected state machine.

## Subsequent Value priority and recovery

The binding is carried through request, hashed fit config, warm role configs,
registrar/stage, and the prepared priority candidate. A warm resume cannot drop
or replace it. Candidate preparation produces separate trainer/evaluator
wrappers, changing only their `value_priority` plan path and SHA. Trainer keeps
the appended owner, evaluator keeps its original owner and registrations.
The Value predictor's primary guard uses the trainer candidate.

The later existing same-plan priority handoff retains its original cooperative
drain, deadline, unit-file, PID and recovery gates. This is distinct from the
immediate trainer registration append. It can resume with one role selected
and the other original or drained, without reverting either registration list
or registering a second trial. All original role files remain immutable.

Requests without `background_guards` keep the strict original common-wrapper
rule, including its existing import-layout check. No legacy request or signed
window acquires this new permission.

## Remaining finite boundaries

- The consumer installer starts its oneshot before enabling its timer. If that
  initial refresh fails after units/receipt are written, verify the exact
  installed stage/receipt/units, then enable the same timer for a normal retry.
  A disabled timer cannot recover merely by waiting. Do not blindly rerun the
  installer: its immutable receipt also records previous unit bytes.
- `prepare_current100k_new_scope.py --once` can write a BLOCKED status and exit
  zero. Inspect its state and source pins before relying on readiness.
- The persistent continuation handles normal first32, guarded fit, stage,
  registration and scheduling. Unknown fit effects still hold; they never
  cause an automatic second fit. Expired finite registration wakes are not
  silently renewed.
- The 120 wake opportunities at 61-second intervals are operational retries.
  The original trial remains a 14-day input window plus one day for outcome
  maturity, with unchanged coverage and alpha rules. Installed/scheduled does
  not prove a future head's same-request pre-OPEN timing or learning efficacy.

## Targeted tests

```sh
PYTHONPATH=scripts:research:research/neuron_learning:tests/python \
python3 -B -m unittest \
  test_current100k_background_guards \
  test_current100k_priority_activation \
  test_current100k_new_scope_continuation \
  test_current100k_continuation_profile \
  research.historical_training.tests.test_current100k_warm_stage -q
```

These tests use private local files and simulated unit/process observations;
they do not start a real service, fit a model, or authorize a trade.
