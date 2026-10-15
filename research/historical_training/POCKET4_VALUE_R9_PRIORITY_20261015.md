# Pocket4 R9 — original Value request priority without preemption

## Rationale

The original value trial3 consumes pinned EUR/USD attribution REQUEST records at
a measured ~120-second cadence. A valid flat-position request needs a genuine
signed prediction within its original **60 seconds**. Existing trainer and
evaluator supervisors both compete with the original Value worker for one
host-local `heavy-worker.lock`. The frozen Value worker has four bounded
original-worker start retries but cannot acquire an already-owned lock.
Increasing CPUWeight does not replace lock admission ordering.

R9 modifies **only the trainer/evaluator admission wrapper**. It does not
replace the frozen Value trial3 worker or its pinned guard config; it never
produces a forecast, owns the model, or grants trade authority.

## Finite new-worker priority admission

`value_priority_admission.py` reads the exact SHA256-pinned original Value
plan and its own pinned producer manifest. It checks private owner-only
attribution files and requires four actual consecutive original request
intervals within 2 seconds of 120 seconds. No synthetic request or new
timestamp is written. Read-only admission decision:

- Trainer: defer new heavyweight training jobs when a verified 120-second
  producer cadence would overlap the original frozen 600-second training
  budget. Long jobs already running are **never interrupted**. Priority is
  finite at the original Value plan's input end.
- Evaluator: defer new heavyweight slots during the first 60 seconds after an
  original genuinely flat request, and in the final 60 seconds before the
  next timestamp estimated from previously observed regular requests.
  Else allow ordinary previous resource/source guard checks and shared flock.
  Current evaluator tasks are **never killed**; old evaluation outcomes and
  original plan remain authoritative.
- Request source absent or stale >240 seconds: do not fabricate a future
  schedule or indefinitely reserve capacity. An invalid private file,
  missing pinned manifest/plan or changed plan identity denies new
  background workers rather than claiming successful verification.
- The supervisor checks this priority BEFORE taking the same original lock,
  then repeats the check AFTER acquiring it to close races. The original
  8GiB available-memory/1GiB swap/PSI/source continuity thresholds and
  one-heavy-worker limit are unchanged.

This favors time-critical prediction over speculative new training, not
economic outcomes. Pending evaluator slots can still become censored when
the priority window conflicts with their original deadline; no backfill.

## Deployment and rollback

A sealed R9 background guard release and NEW private configuration live under
`~/.local/share/heptatrader/resident-resource-guard/releases/r9-value-priority-...`
and `~/.local/state/heptatrader/operator-evidence/pnl-r9-value-priority-cutover-20261015`.
Both keep the exact old original Laya configuration and SHA256-pinned
model/runtime code. The Value original guard config is NOT changed.

The user-level systemd overrides must sort after the older `zzzz-...`
drop-ins to actually apply. They remove the one-time ExecCondition gate
(steady worker admission still runs inside the supervisor); preserve
the same shared lock, paths and runtime. During a safe cutover only:
set `Restart=always`, send **SIGUSR1 to the main supervisor**. Its
registered handler drains existing research children and cleanly exits.
Systemd restarts the new read-only supervisor with the sealed R9 code.
After both new processes are observed healthy, restore
`Restart=on-failure` and the original 30-second restart delay.
No forced SIGTERM/SIGKILL, no order, no Execution restart.
Prior units and sealed source remain for rollback.

## Qualification and limitations

A source-only policy cannot prove improved inference. Verify post-cutover
with ORIGINAL new flat REQUEST identity, original signed prediction receipts,
unchanged 60-second deadline and the complete denominator of early/late/
unresolved requests. Do not count historical reruns, stale quotes, unit tests
or synthetic fair scheduling as live Value performance.

Production PAPER broker position, original order identities, OMS journal and
cash generation recovery barrier were read only. No mutation or strategy
cutover follows from research admission.
