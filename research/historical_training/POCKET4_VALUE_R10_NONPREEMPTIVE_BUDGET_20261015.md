# R10: protect original 60-second Value deadlines across full evaluator leases

## Actual R9 frontier, 2026-10-15

Independent pinned-original REQUEST and signed-commit replay after the
R9 cutover (2026-10-15 12:12:58 UTC+8) found eight real original requests by
12:29:42, **all eight nonflat**. Seven were mature. There were **zero
eligible mature flat requests** in that observed interval; neither an
improvement nor a deterioration of forward Value seal rate can be claimed.

R9 correctly held off new training, but only reserved the evaluator lock
for 60 seconds BEFORE a forecast's estimated input slot. A fresh Laya
evaluator child is non-preemptive on the same host-global heavy-worker flock
and the pinned `laya_cell.resident_evaluate.Evaluator.reap()` permits **240
seconds** before timeout, then an additional bounded shutdown wait. Thus a
slot launched 100 seconds before the next expected Value request could
hold the lock well past that request's *actual* 60-second signed deadline.

## R10 source fix

- Derive reservation from a fixed 250s evaluator child budget (240s plus
  shutdown/scheduler margin), NOT from the Value prediction's 60s deadline.
  The trainer remains bounded at 620s (original 600s training wait plus
  15s termination margin).
- Existing verified original producer cadence is ~120s. Consequently,
  while that cadence is verified, there is NO gap long enough to safely
  start another non-preemptible 250s evaluator child. **Defer NEW Laya
  evaluation work** until the producer becomes stale/irregular or the
  original Value trial input end is reached. This intentionally trades
  off fresh Laya evaluator coverage to prevent new work obstructing the
  higher-priority original Value request.
- Do not terminate, signal or shorten a running Laya child. Old signed
  outcomes and timeouts remain unchanged and any censored evaluation
  remains honestly censored. No automatic promotion.
- Do not invent future requests: use only a SHA256-pinned original trial
  plan, SHA256-pinned attribution manifest and real consecutive original
  REQUEST rows. `next_expected_from_historical_cadence_ms` is explicitly
  an ESTIMATE and carries no request evidence or inference authority.
- Do not reserve against a predicted timestamp after the trial input end.
  Release naturally when the original producer becomes stale/irregular.
- Keep the original `heavy-worker.lock`, original source/memory/PSI
  admission guards, original 60s prediction deadline, signed evidence,
  original Value worker and PAPER strategy unchanged.
- Gate acquires the same old flock nonblocking only after priority
  admission, then RECHECKS both source and priority after acquiring it.

## Separate deployed release

The R10 systemd cutover is scoped to the **Laya evaluator only**.
Its sealed release copies the previously DEPLOYED R9 files byte-for-byte
(`common.py`, `resident_live_source.py`, `resident_resource_guard.py`)
and replaces only SHA256-pinned `value_priority_admission.py`. This
avoids changing an unrelated newer main-branch source-health optimization
while upgrading the running research supervisor. The trainer remains on
the original R9 sealed release: its policy already defers new 600-second
jobs under the same cadence.

The original evaluator owns any pre-existing task and lock until it
naturally completes. The already-registered `SIGUSR1` **drain** handler
stops admissions and exits only after the last child has been reaped.
It is distinct from SIGTERM (which could terminate children). A
temporary `Restart=always` switches the evaluator after natural exit;
restore `Restart=on-failure` and `RestartSec=30` once the new PID and
pin are actually verified. Do not touch Production Execution or drivers.

Evidence path:
`~/.local/state/heptatrader/operator-evidence/pnl-r10-evaluator-budget-20261015/`.

## Tests and remaining real qualification

- Synthetic original requests with regular vs irregular periods, stale
  sources, missing/changed pinned manifests, private journal controls,
  no floating trial, full non-preemptible budget, and after-trial release.
- Latest-main historical-training Python: 681 passing tests.
- Non-native Laya/Value suite: 314 passing and one skipped. Native
  fixture-dependent classes not included.
- A real Value qualified forward sample exists only for a genuine
  **flat-position** original REQUEST plus signed prediction committed
  before its original 60-second deadline. All nonflat requests remain
  outside the denominator; observer must never turn a test or estimate
  into a model/economic result.

This is research scheduling, not risk authorization, an IB account cash
fix, a new broker connection epoch, or financial model qualification.


## 2026-10-15 Pocket4 installation receipt

The evaluator R9 main PID 3448782 was found with one actual Laya child
holding the original heavy-worker flock. No force-stopping operation was
performed. The new unit override was first staged (while the old PID
remained running), then only the registered `SIGUSR1` supervisor drain was
sent. The child naturally completed before systemd started the new evaluator
PID **3492290**. Its original `Restart=on-failure` / 30s retry policy was
restored by daemon reload without a further process restart.

Exact runtime source is
`~/.local/share/heptatrader/resident-resource-guard/releases/r10-evaluator-budget-199fc70d540c14a0`;
the previous R9 resident guard and source reader are byte-for-byte unchanged.
The sole runtime change is `value_priority_admission.py` SHA256
`199fc70d540c14a036688513f47e88135109daa722ac146bda77efc1a619d70d`.
The new four-file inventory is bound in
`~/.local/state/heptatrader/operator-evidence/pnl-r10-evaluator-budget-20261015/guard-r10.json`,
and the old frozen Value worker and its original guard configuration remain
untouched. The Laya trainer continues to use its separate R9 pinned release.

Actual R10 systemd receipt:
`~/.local/state/heptatrader/operator-evidence/pnl-r10-evaluator-budget-20261015/final-cutover.json`.
No original message, prediction, order or qualification was backdated,
republished or fabricated. No real post-R10 qualified flat request seal
improvement has yet been demonstrated; this is a scheduler-bug closure,
not a promotion decision.
