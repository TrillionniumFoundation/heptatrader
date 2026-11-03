# Current100k pre-fit retirement and independent bootstrap scope

This is a separate, explicit lifecycle operation. It does not complete a trial,
activate a driver, authorize an order, change a preparation request, or transfer
old training rows to another producer. The ordinary producer maintenance and
fresh native flat checks remain the operator's separate prerequisites.

## Eligible boundary

`scripts/retire_current100k_pre_fit.py` defaults to a read-only check. It opens
existing mutex files without creating them and takes nonblocking locks in the
frozen executor's order: selected v2, its original v1 predecessor if present,
then preparation. A busy lock or active service is a hold, not a force stop.

The old request and preparation SHA must match. The old executor is clean at
its own pinned commit. The helper runs that executor's own `--check-only` with
its original `PYTHONPATH` and verifies its returned exact service/timer bytes.
It does not import a new continuation/precheck implementation to validate the
old request or impose a new dependency inventory on the old runtime.

The current controller must have a fresh `WAITING_NEW_PRODUCER_32_SETTLED_LABELS`
status, `training_started=false`, `trial_registered=false`, and fewer than 32
settled rows. The current consumer must be fresh, coherent and for the same
producer, period and behavior model. The preserved count comes from its actual
dataset; it is not fixed at 10 or 11. Every row ID and canonical row hash is
checked, and every original request/input/reward/episode identity is retained.

Only known pre-fit controller/preparation files are accepted. Any frozen32,
READY, fit-stage, fit-attempt, warm-stage, selection, registration wake,
CONTINUATION_COMPLETE, unknown artifact, fit output or forward root blocks the
operation. The predecessor must already be idle and its timer retired.

The intended successor manifest must have a different SHA, point back to the
old producer, retain period/instrument/behavior fields, and explicitly select a
PRICE bootstrap gate. This is an identity binding only. The helper does not
certify the gate implementation, run native maintenance admission, or activate
that successor.

## Review and execute

Use the newly integrated clean source containing this helper and the reviewed
gate. `NEW_INTEGRATED_HEAD`, `PREPARED_NEW_MANIFEST`, `NEW_MANIFEST_SHA`, and
`DEADLINE_MS` below are explicit operator inputs, not automatic selections.
The deadline must be in the future and at most 15 minutes away. Use the same
inputs for check and retirement within that finite action window.

```sh
/usr/bin/python3 -B scripts/retire_current100k_pre_fit.py \
  --request /home/alex/.local/state/heptatrader/operator-evidence/fullpush-20261102/current100k-newscope-continuation-v2/CONTINUATION_REQUEST.json \
  --sha256 2e65d1be19d60dea5704fa6826fd3935bef162322722ef74ec335c3fb6c19c6a \
  --expected-source-commit NEW_INTEGRATED_HEAD \
  --next-producer-manifest PREPARED_NEW_MANIFEST \
  --next-producer-sha256 NEW_MANIFEST_SHA \
  --output-directory /home/alex/.local/state/heptatrader/operator-evidence/fullpush-20261102/current100k-pre-fit-retirement-2e65d1be19d6 \
  --deadline-ms DEADLINE_MS
```

After the new release has passed its separate review and normal maintenance has
reached fresh native flat, repeat the same command with `--retire`. Do not hold
maintenance indefinitely waiting for positions to close. The helper:

1. Revalidates the locked boundary and saves exact old controller/consumer
   status and dataset bytes plus both original CPU-check receipts in a separate
   output directory.
2. Fsyncs `RETIREMENT_INTENT.json` before any systemd effect.
3. Runs only `systemctl --user disable --now` for the selected request's exact
   timer. It never stops/kills a running service, and waits at most 15 seconds
   for the service to be idle.
4. Writes a separate `90-pre-fit-retired.conf` drop-in for that service containing
   `ConditionPathExists=!<retirement-root>/RETIREMENT_INTENT.json`, reloads
   systemd, and verifies the original base unit/argv/environment, exact drop-in,
   loaded state, inactive/disabled timer and zero service PID.
5. Writes `PRE_FIT_RETIRED.json`. The old scope is explicitly incomplete, with
   zero rows credited to a successor. Old request/data/source files and all
   registered trial roles are unchanged. It does not write the old controller's
   `CONTINUATION_COMPLETE.json`.

The separate condition prevents accidental starts through the selected v2
service while the immutable intent exists. It does not claim to prevent a user
from deliberately editing/removing systemd configuration. The already retired
v1 service is checked but not modified by this operation.

## Unknown effects and rollback

A failed or timed-out systemctl call is not proof that it had no effect. The
helper returns `PRE_FIT_RETIREMENT_HOLD_INSPECT_EXACT_INTENT_AND_UNITS` and leaves
all evidence. It never automatically re-enables an old timer. A complete exact
intent can be explicitly resumed using the same arguments within the original
deadline; current units, pins and saved snapshot hashes are rechecked.

Partial snapshot staging before an intent causes a hold and has authorized no
systemd effect. After inspecting that boundary, a separate fresh output directory
can be selected without deleting the partial evidence. This tool does not claim
automatic recovery from every crash. An expired action requires a separate
review; it is never silently given a renewed deadline.

Rollback is not implemented automatically. A separate review must establish the
old producer is actually active again through normal maintenance, native state
is safe, no frozen cohort/fit/new trial has appeared, and all successor
continuations are idle. Only then may the exact retirement drop-in be removed
and the old timer deliberately selected again with a new recovery receipt.
Never delete the intent to bypass the condition. Intent, receipt, old partial
scope and all old registrations must remain available.

## Native entries for the new producer

After normal `install_decision_attribution.py --prepare-upgrade` and
`activate_prepared_driver.py` have actually verified the new producer:

| Stage | Existing entry and binding |
| --- | --- |
| New Current100k consumer | `scripts/prepare_current100k_consumer_scope.py --repo ... --expected-head ... --old-config ... --old-config-sha256 ... --producer-manifest ... --producer-manifest-sha256 ... --evidence-root NEW_SCOPE_STAGE`. Its receipt gives the exact `python -B -m historical_training.install_consumer --config ... --receipt ...` argv. The new output is `.../scopes/<new producer SHA>`; prior outputs/configs remain. |
| Fresh first32 preparation | `scripts/prepare_current100k_new_scope.py --consumer-config NEW_CONFIG --consumer-config-sha256 SHA --producer-manifest-sha256 NEW_SHA --expected-source-commit NEW_HEAD --legacy-plan ...` for every actual published prior trial, `--output-directory NEW_PREPARATION --once`. No old preparation is rewritten. |
| Independent v2 continuation | `scripts/stage_current100k_new_scope_continuation.py` with new source/controller/fit/forward paths, the new preparation SHA, original fit template/prior-stage pins, the accepted effective guard config, `--worker-profile cpu-standby-v2`, both parity03 report/lifecycle pins, and `--install`. **No `--supersedes-request`**: that contract supports only the original v1-to-v2 same-preparation handoff. |
| Request diagnostics | `scripts/observe_current100k_requests.py` may explicitly select the new manifest. Until its own Value plan exists, it must report no same-producer registered Value plan rather than borrowing trial4/old scope. The PRICE baseline seal remains a separate task/identity. |

Stop the global consumer timer and wait for an existing consumer oneshot to exit
normally before replacing its exact unit through the consumer installer; retain
its old scope. Verify the new loaded config, scope, timer and first read-only
refresh before relying on future rows. A source-change race on cold refresh is
a bounded normal retry with provenance retained, not permission to weaken it.

The new source must support the gate's six-file producer runtime. Add
`hepta_price_bootstrap_gate.py` to the **new** precheck `SUPPORT_FILES` and priority
activation `MODULE_FILES`. `stage_current100k_warm_flat.assemble_files` then
includes it in new fit/worker closures. The old 611 executor and its original
inventories are retained. Re-run the full original hardware-evidence validator
against the new source; a changed tokenizer SHA cannot reuse parity03 by fiat.

If the old 3c6 scope is retired before any trial5 registration, it consumes no
new trial allocation. The new preparation still derives its expected trial from
the complete actual published history; it must not invent a completed old trial
or reset alpha spending. Any observed old trial5 side effect blocks this pre-fit
branch rather than stopping that registered trial.

## Laya's separate producer filter

`resident_rewards.source_bindings` enumerates
`decision_attribution_registrations[instrument]`. The Current100k exporter
explicitly replaces that source mapping with its one selected new registration,
so its separate consumer does not need old rows or a Laya config change to
attribute its own producer. Laya's general trainer does need the new manifest
appended to its EUR.USD registration list; preserve every old EUR.USD entry and
the complete BTC.USD configuration.

There is no dedicated append-registration CLI in this repository. The existing
configuration boundary is a new immutable original config, differing only by
that one appended registration, and a new wrapper pinning it through
`historical_training.resident_resource_guard.verify_config`. Keep the actual
frozen model/runtime, HIP/Python, guard module hashes, resource rules, priority
plan and directories unchanged. Do not use a general runtime reinstall as a
registration-only update.

The existing live guard's normal drain is `SIGUSR1` to the verified trainer main
PID: `resident_resource_guard.main.request_drain` sets draining and
`_drain_trainer_worker` waits for natural completion/reap under the original
locks. After its exact process exits, select the new immutable wrapper for that
trainer's normal ExecStart, preserving its service resource/drop-in settings.
The invocation remains `python -B -m historical_training.resident_resource_guard
--config NEW_WRAPPER --role trainer` in the **existing guard/runtime environment**.
Read back the actual config/PID and a normal reward sync. Do not replace the
running evaluator or rewrite any registered Laya plan as part of this append.

## Time boundaries

The continuation's 120 registration/activation wake opportunities, 61 seconds
apart, are a finite operational retry calendar. They are not 120 valid forecasts
and are not the formal trial length. The original financial forward rule has a
14-day input window plus one day for outcome maturity. A controller completion
receipt proves registration and scheduling only. This pre-fit branch retires
an unregistered scope and does not require waiting for a nonexistent trial's
15-day window.

## Targeted verification

```sh
PYTHONPATH=scripts:research:research/neuron_learning \
  python3 -B -m unittest research.historical_training.tests.test_current100k_pre_fit_retirement -v
```

The fixtures include the exact production 2e65 request bytes and its actual
old-executor check-only unit receipt. All service effects in local tests are
simulated; no test operates a real user service or producer.
