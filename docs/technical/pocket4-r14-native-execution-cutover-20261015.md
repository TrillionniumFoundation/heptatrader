# Pocket4 R14 PAPER native Execution controlled cutover (2026-10-15)

## Deployment identity

R14 was actually installed onto the existing user-systemd
`heptatrader-execution.service` by explicit owner direction rather than
waiting for a natural zero-position maintenance window. Only a separate
last-sorted `ExecStart` drop-in was added; the earlier vendor systemd
unit, original candidate binary and existing PAPER startup hold guard
remain available and unchanged.

Actual native Release binary:
`b79eb2808a98e747443065e51783b025d22ddc24fb6cd34c151ddf1b7a026468`

Source commit before this receipt:
`c269bc558aa07054d3bf347d69bf4a90e647bf70`, based on latest
GitHub main `fcb26fd6e511b175aaf8bbcd59a93ff34c2ff9aa`.
The build uses pinned IB API C++ SDK 10.30.01 and the original native
Intel BID decimal library; **7/7** IBAPI-enabled native suites and
**10/10** independently built IB recovery suites passed beforehand.

## Original owner containment and actual Broker evidence

The current autonomous FX and BTC PAPER driver services were stopped via
systemd's registered graceful SIGTERM path, **not killed**, only after
checking no pending original send and no active Broker orders. The same
original operator-signed PAPER hold marker was then engaged by the
preexisting `hepta_paper_hold_rearm.py`. No risk-protection bypass,
broker order or synthetic flatten was used.

The original Execution service was restarted with the new SHA256-pinned
binary under the unchanged `ExecStartPre` guard. A distinct new process
and service epoch were observed. Its **real** Broker session was connected,
terminal/cash/account/positions observations were complete, and the
previously failed cash-generation recovery audit now had
`recovery_barrier_complete=true` and an empty recovery reason.

The original nonflat PAPER FX position and flat BTC state survived the
service-epoch change, without active or pending Broker orders, altered
terminal identities or additional OMS order send attempts. After both
drivers passed their existing `--reconcile-only` mode, the same
autonomous FX/BTC service units were resumed. Independent native
`system.get_health` confirmed source IB authoritative, live PAPER
execution transport ready, no owner fencing, terminal download complete,
and `mutation_ready=true`.

At final acceptance **96 consecutive new-process runtime observations**
over ~475 seconds had an intact true recovery barrier. The original
PAPER hold marker **remains engaged**. This is deliberate: a successful
Execution binary switch and Broker recovery do not, by themselves,
authorize removing the independent new-risk guard. New risk is still
subject to original operator, quote, risk, session and strategy admission.
No manual `paper-on` was performed.

## Honest qualification limit and rollback

- This is a real **PAPER** deployment, not a LIVE financial trading approval.
- It proves controlled restart/identity continuity and the bounded
  observed recovery interval, not an overnight endurance qualification.
- The original binary and original `ExecStart` drop-in remain intact.
  To roll back: first safely pause both entry-producing PAPER driver
  services and preserve the existing hold; remove only the R14
  `99-r14-current-main-native.conf` override; reload user systemd;
  restart Execution with the unchanged hold prestart; recheck original
  OMS/active order/execution identities and Broker position, then resume
  drivers only after original reconciliation passes. Never force-flatten,
  replay old command identities or disarm the hold to make rollback pass.
- There are no account IDs, passwords, tokens, unredacted cash amounts or
  personal order identities in this public document. Exact private
  before/after receipts remain on the authorized Pocket4 host under
  `~/.local/state/heptatrader/operator-evidence/r14-immediate-deployment-20261015/`.
- The related original Value R12 bounded-expiry watcher was not modified
  or restarted by this cutover.

This deployment qualifies native R14 on a real Broker connection. Source
merging/longer-term trading admission remain separately reviewable; do
not use the GitHub merge check to infer external Broker authorization.
