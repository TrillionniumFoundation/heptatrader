# Resident memory/source admission and safe maintenance hold

This change is research-only. It does not resume Execution, either FX/BTC driver,
Tool Gateway, order ownership or strict NAV, and does not change frozen candidate
f7f18c or the 24-slot campaign. Existing training objectives, candidate bytes,
slot dates, previous observed/censored outcomes and native journals are retained.

## Incident and the actual containment operation

At the start of this iteration pocket4 had 23,120 MiB RAM, 7,021 MiB available
and effectively no free swap. The evaluator still created cold model workers
although the last usable source quote was nearly two hours old. Its original
supervisor allowed two prediction workers, loaded models before checking the
fixed-slot input, and had no shared admission lease with the trainer. Both
services had 6 GiB per-unit limits. These caps did not prevent prior GLOBAL OOM;
per-unit memory.max is not a guarantee about total concurrent UMA/host demand.

The trainer's current job was already complete. The scheduler contained 23
COMPLETED and 1 historic FAILED job, with no ADMITTED/RUNNING job. A bounded
operator drain temporarily stopped only each supervisor's dispatch thread group,
using pidfd identity checks. Trainer had no child. Evaluator had two existing
children, PIDs 1786245/1787406; they received zero signals, naturally exited with
status 1 on their original unavailable/expired input path, and became zombies.
Only then was each idle supervisor stopped via systemd. The evaluator drain took
115.206 seconds. Original result files were not marked successful or replayed.
A consistent SQLite backup and candidate/adapter hashes were retained; all job
rows stayed identical. The routine resumes any not-yet-stopped supervisor on
failure/timeout. It does not raise swap limits, drop caches or kill other apps.

The post-drain available RAM readback was 10,907 MiB, while swap still had only
about 6 MiB free. That is containment of new research work, NOT full memory
recovery or reliable-market restoration.

## New boot during the operation — do not conflate the epochs

pocket4 subsequently booted at 2026-10-08 14:36:09 UTC, boot ID
47e4eb01-ebdb-44dc-a20d-8e5eaf03676c. This iteration issued no reboot/power command;
the initiating actor is not established. At 14:41:46 UTC the host showed 14,978
MiB available and 8,191 MiB free swap. That reset must NOT be attributed to the
drain or to this guard. Runtime PID/restart counters before/after a reboot are
not comparable as continuity evidence.

Enabled learning services had automatically restarted. The first installer
attempt therefore stopped at NO_HOT_RUNTIME_CHANGE before touching their units.
The failure log remains. The new trainer had already naturally reached 24
COMPLETED / 1 FAILED with no running/admitted job. A fresh boot-scoped checkpoint
and a second idle drain were taken; no active prediction child remained at that
fence. No completed training or old evaluation evidence was overwritten.

## New guard and its scope

`resident_resource_guard.py` is an external supervisor-admission wrapper. It
imports the unchanged, hash-pinned original resident Supervisor/Evaluator;
original config, runtime files, model/optimizer, result measurement and native
identity checks are not patched. The changed coordinator and its own source
inventory are explicitly recorded, not represented as an unchanged executable.

For each NEW heavy worker it requires all of:

- no manual resource-hold marker;
- >=8 GiB MemAvailable and >=1 GiB free swap;
- full memory PSI avg10 <=1% and avg60 <=0.1%;
- the original period/schema and exact source rules, >=20/24 usable, <=4 missing;
- status age <=120 seconds and original quote age <=5 seconds, with no future
  timestamp acceptance;
- a shared nonblocking, private, no-follow/CLOEXEC lease between trainer and
  evaluator, with at most ONE heavy research child across those roles.

The lease is taken before worker admission and telemetry is rechecked afterward.
A denied trainer never calls the original admission/data-consumption step. A
denied evaluator does not load a model; the original slot deadlines and ordinary
censoring logic remain. Denial reasons are retained separately from fixed-slot
outcomes. Original prediction/measurement behavior is used when admitted. The
resource thresholds are conservative admission policy, NOT a proof that an
unrelated concurrent application can never cause OOM after admission.

SIGUSR1 to a guarded supervisor requests no-new-work natural drain. Unlike its
original TERM handler, it does not signal the active child; the supervisor exits
only after the original step/reap finishes. Ordinary TERM behavior remains the
original behavior, so maintenance must not blindly issue stop while work is
active. Positive live heavy-worker admission is NOT tested in this incident
window; tests use synthetic supervisors/telemetry and real private file locks.

## Installed state and actual negative admission checks

Only the two drained research unit files receive an ExecCondition and a guarded
ExecStart. Previous files are saved for rollback; original old-source config and
all runtime hashes are unchanged. A new manual-hold.json is retained in the
separate resource-guard state directory. Real start attempts for both units were
skipped with Result=exec-condition and MainPID=0: ZERO heavyweight worker starts.
The services were not disabled or masked. This check survives their normal
startup path on another boot, but no new reboot was performed to test that.

Installed resource state:
`~/.local/state/heptatrader/neuron-learning/resident-resource-guard-20261008/`.
Pinned package/config locations and before/after unit hashes are in the private
`runtime-conditions-20261008/guard-install.json` receipt.

Restoring the original unit files is a SOFTWARE rollback, not permission to
resume resource-intensive work while the incident persists. Clearing the manual
hold alone does not pass the source/memory checks. Resume only after original
source/owner recovery and a reviewed memory reserve; never replace old missing
quotes or move failed campaign slots. The guard does not clear any trading lock,
submit an order, grant canonical owner authority or claim NAV completeness.

## What is not closed here

The original source is still unavailable (0/24) after the new boot. Native
Execution/Gateway and driver/socket states must be read from the current boot,
not inherited from earlier snapshots. The #178 native recovery candidate has
separate live original-owner, quote-loss/farm and rollback requirements. Earlier
blocked handoff/read paths are not worked around through a second client or a
fabricated feed. No Broker query or source substitution is made by this guard.

The finite frozen campaign remains installed with its original schedule, model,
thresholds and previous observed/censored records. Failed source admission stays
censored; no slot is retried at a more favorable time. Matched English/multilingual
results and BTC terminal/NAV evidence remain separate research/account scopes.
