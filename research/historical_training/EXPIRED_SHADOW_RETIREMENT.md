# Retire expired shadow-model residency before resuming new learning

This continuation starts from e2f2800 (resident source/resource admission), not
from the older 07460bb report. The host had rebooted before the first readback.
Resident trainer/evaluator were inactive behind the installed original resource
hold and source gate. The retained trainer status reported 24 completed jobs,
1 historical failure, 0 running and 0 admitted; no new training job is created
or interrupted by this maintenance.

## Concrete remaining memory cause

Two old fixed-plan predictor units reloaded models after boot even though their
original experiments had ended. The installed implementations load their Laya
backbones before looping over original slot dates and remain resident forever.

- Paired-price plan 859b87d7...: 24 original slots, ended 2026-10-08 02:48:45.245 UTC.
- Temporal-fusion plan c7717faf...: 12 original slots, ended 2026-10-07 18:23:16.921 UTC.

These are NOT the frozen f7f18c six-day campaign or the resident trainer. Startup
preflight verified each exact original plan, rule, end time, and every original
input slot's existing prediction/failure identity. All original slots were
already sealed, the end was more than a minute in the past, and each predictor
cgroup held only its main process. No running inference child or unsealed input
slot was used as grounds for termination.

The two cgroups together charged 2,868,150,272 bytes (2.671 GiB) immediately
before retirement. With all evidence retained, only these two predictor units
were gracefully stopped. No forced SIGKILL was permitted. Their model files,
legacy predictions/failures/outcomes and plan files were not deleted or changed.
Resident training/evaluation processes and the frozen campaign were untouched.

Host MemAvailable changed from 11,817,713,664 to 16,572,846,080 bytes in the
readbacks; that host-level delta includes concurrent workloads/cache effects and
must NOT be attributed entirely to retiring these models. Swap reserve was about
7.32 GiB; full memory PSI avg10/avg60 was zero. These observations do not prove
that every possible global OOM cause is permanently eliminated.

## Boot-safe, pre-model lifecycle condition

`legacy_shadow_lifecycle.py` is pure standard-library code plus the existing
strict evidence helpers. It imports no Torch, model, GPU, broker or service
controller. It validates exactly the paired-price and temporal-fusion fixed
plan identities and timing contracts; the separate f7f18c campaign is refused
by this interface. Unsupported kinds, wrong hashes, altered authority/timing,
boolean timestamps/counts and clock rollback fail closed.

The installed immutable package contains only that helper, common.py and a
minimal entrypoint. Each of the TWO old predictors has its own drop-in:

```ini
[Service]
ExecCondition=/usr/bin/python3 -B /pinned/package/entry.py --plan /original/plan.json --plan-sha256 ORIGINAL_SHA --kind paired
SendSIGKILL=no
TimeoutStopSec=30
```

The temporal unit uses kind temporal. Existing ExecStart and original code/model
pins are unchanged. Invalid evidence exits 255; an expired plan exits 1, causing
systemd to skip model startup. A real subsequent start request for each unit
returned inactive/MainPID=0/Result=exec-condition. This is a verified denied
startup, NOT a running predictor or a successful new prediction.

The drop-ins are 90-expired-fixed-plan.conf under the corresponding predictor
unit directories. Existing evaluators are not retired: they may still reconcile
old outcome evidence. This helper alone is not a general live source/resource
admission controller; resident_resource_guard retains that responsibility for
new training/prediction. Do not reuse an old unit with a different plan without
an explicit new plan binding and lifecycle review.

## Evidence-report exception and recovery

The action script completed both graceful stops and both real blocked-start
checks, then raised KeyError at its final report because a systemd TIMER does
not expose MainPID. The operation was NOT rerun. A separate readback checked
unit state, original files, preserved resident processes and the actual boot
conditions; it recorded this exception and verified completion. The original
failure log remains alongside retirement-before.json and retirement-readback.json.

## Original source recovery stays separate

The other original-owner recovery transaction brought Execution/Gateway/FX back
to active during this continuation. No trading service start, deployment,
Broker query, order, cancellation or ownership change was performed here.
A service's active flag or a new last-quote timestamp is not source continuity.

The original source-readiness daemon began returning current quotes, but its
24-probe coverage was still rebuilding (3 usable at the early post-retirement
readback). The existing 20/24, five-second freshness and path-gap rules are not
relaxed. A finite no-Broker readback records memory/PSI, original service IDs and
source judgments repeatedly. It does not rewrite any source record or confer
NAV/model efficacy authority. The manual learning hold remains until the source
and memory conditions are genuinely met and original pending work is reconciled.

The original 24-window campaign remains 1 observed / 2 censored / 21 future at
this continuation's initial campaign readback. Failed slots are not replayed,
rescheduled or backfilled. Frozen candidate, plan, timer and old receipts stay
byte-identical. Its later scheduled work is subject to the unchanged source gate.

## Verification

New synthetic tests cover live/expired boundaries, both exact plan types,
read-only identity, clock/timing/authority tampering, sealed-slot completeness,
noninterference with training kinds, immutable bytes and no Torch import.
No test loads an actual checkpoint or exercises the Broker. Exact current commit
Core/Source/CI results are recorded separately; previous green heads are not
inherited. Operational evidence is stored privately under
`~/.local/state/heptatrader/operator-evidence/runtime-source-followthrough-20261008/`.
