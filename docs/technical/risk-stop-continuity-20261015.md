# P0: PAPER owner stop-request continuity (2026-10-15)

This is a **small systemd stop-request shield**, not a broker-native stop-loss and
not a fix for independent risk continuity. See incident
[heptatrader#331](https://github.com/TrillionniumFoundation/heptatrader/issues/331).

## Verified incident

On 2026-10-14, the original EUR/USD PAPER BUY opened at **08:09:47.939
UTC**. The FX and BTC driver units were both explicitly stopped by systemd
starting **08:23:49 UTC**, fully stopped at **08:23:56 UTC**. The FX driver
did not start until **12:57:45 UTC**, and the correlated original close
execution was at **12:57:47.344 UTC**. Actual gross/fee/net were
-646.119320 / 4.000007 / **-650.119327 USD**. The pinned original source
contains a 20 bp MAX_ADVERSE threshold, but the observed fill-to-fill
difference was 64.61 bp. This does **not** establish a counterfactual
20-bp execution price, the historical exit trigger, or who stopped the units.

## Immediate no-stop design

The original user units use `PartOf=heptatrader.target`; a target stop
propagates to the FX and BTC drivers, Execution, the two Execution sockets,
the runtime directory oneshot and Tool Gateway. Some are also connected
through `Requires`, so protecting only the driver is insufficient.

For the **entire risk-exit runtime path**, the unit source removes that
`PartOf` edge and enables `RefuseManualStop=yes`. The Pocket4 installer
`scripts/install_risk_stop_fence.py` also shields the external
`ibgateway.service`, which is not a repository-owned unit. Exact affected
units are hardcoded; caller cannot choose arbitrary units.

The installer:

- Requires the current user (non-root), the existing owner-private PAPER/4002
  profile (no symlink or duplicate broker identity fields), and all eight
  named units already active.
- Publishes one identical user-owned `95-risk-stop-continuity.conf` drop-in
  for each unit. Refuses symlinked/conflicting paths and never overwrites a
  modified existing drop-in.
- Runs only `systemctl --user daemon-reload`, **not** start, stop, restart,
  enable, disable, an IB API command or any order command.
- Re-reads effective `RefuseManualStop=yes` and empty `PartOf` on every
  unit, checks original PID and InvocationID are unchanged, and preserves
  an immutable local activation receipt.
- The private write-once activation receipt is part of the **same install
  transaction**: an ENOSPC/fsync failure must restore the original stop
  policy, rather than leave a shield running with no audit identity. Partial
  drop-ins and partial receipts are removed on failed writes.
- Rolls back only its newly created exact-byte drop-ins if effective
  verification or receipt persistence fails; it never attempts a service
  stop during rollback. Pre-existing verified drop-ins remain unchanged.

This means **direct `systemctl --user stop` of a shielded unit is rejected**.
A manual stop of `heptatrader.target` also no longer propagates to the
eight shielded units. On Pocket4 systemd 259 both properties were
confirmed against an independent synthetic user unit and synthetic target:
direct manual stop was rejected and stopping the test target left the
test process alive.

**This deliberately changes maintenance semantics.** A normal rollout
that stops one of these units now fails closed. Do not bypass the shield
by unreviewed `kill`, `systemctl kill` or temporary deletion while a
PAPER position or original command is outstanding. A future controlled
maintenance procedure must first establish a broker-owner-enforced
*no-new-entry* state, exact-account flat/no-pending/no-active-orders
evidence, and a durable rollback plan; simple read-then-stop is
check-to-stop race-prone. This implementation does **not** claim to
provide that maintenance unlock procedure.

## What remains unprotected (P0 still open)

- Host power loss, logout/termination of the user manager, SIGKILL,
  application failure/repeated crashes and broker disconnection.
- A failed/stale quote source, a disconnected IB API, or unavailable
  Gateway/Execution even while systemd shows services active.
- A previously accepted order completing after the last account snapshot.
- Exit triggers implemented only inside the strategy process. There is
  still **no verified independently owned broker-native protective stop**.
- systemd dependency failure/shutdown can override `RefuseManualStop`.

The next production gate is a tested Execution-owned/broker-side
reduce-only protective authority that survives driver absence without
creating duplicate commands, false zero fills or conflicting owners. No
PAPER orders or involuntary flatten are authorized by this shield.
