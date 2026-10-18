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

## Immediate manual-stop shield on live Pocket4

The currently installed user units retain `PartOf=heptatrader.target`.
A target stop can cascade to FX/BTC Drivers, Execution and its sockets,
runtime oneshot and Gateway. This is the failure mode implicated in #331.

**Corrected design (verified on real systemd 259):** A drop-in may add
`RefuseManualStop=yes`, but a dependency such as `PartOf=` **cannot
be removed with an empty drop-in**. Systemd's official unit manual
requires a full unit replacement to remove an existing dependency.
The first installer correctly rejected the ineffective old drop-in and
rolled back without stopping processes. This corrected installer does
not use a fragile full-unit replacement while the owner is trading.

Instead, install the same `[Unit] RefuseManualStop=yes` drop-in on
**all eight existing risk-chain units AND `heptatrader.target` itself**.
Direct user `systemctl stop`/restart of the shielded risk units is
rejected; manual stopping or restarting the target is rejected too.
Existing `PartOf` edges are **preserved**, not falsely reported removed.
An inactive target may be shielded without starting it.

The installer `scripts/install_risk_stop_fence.py`:

- Requires the current non-root owner, existing owner-private PAPER/4002
  profile, eight already-active risk-chain units and a loaded target
  (which can be active or inactive).
- Publishes the owner-only `95-risk-stop-continuity.conf` drop-in for
  each of nine exact names, including external `ibgateway.service` and
  `heptatrader.target`. No arbitrary unit input is accepted.
- Uses only `systemctl --user daemon-reload`, **not** stop/start/restart,
  IB API calls, new orders, or changes to original unit ExecStart.
- Re-reads effective `RefuseManualStop=yes` for all nine and verifies
  exact original `PartOf`, active state, PID and InvocationID remain
  unchanged. Existing conflicting/symlinked drop-ins are refused.
- Persists a private, write-once activation receipt with the same rollback
  boundary as all newly created drop-ins. On failure, only exact bytes
  created by that run are removed, followed by another daemon-reload;
  no risk service is stopped in rollback.

**Real Pocket4 positive test:** independent synthetic service with
`PartOf=<synthetic-target>` retained the same main PID while
`systemctl --user stop <service>`, `stop <target>`, and
`restart <target>` were all rejected. The synthetic units were
removed after the test. The corrected installer then actually enabled
the nine live stop-request guards without restarting any process;
its immutable receipt is at

`~/.local/state/heptatrader/operator-evidence/risk-stop-target-fence-activation-20261015/stop-shield-1792119183745602581.json`

with SHA256
`fa8d47eb53aa915db536263ee9f5f1f8b2f9f179686a18472c6f1990fee010d4`.

An **independent second read** verified all nine unit modes/properties and
identical 0600 owner-owned drop-ins. Pre- and post-install InvocationID
and main PID match; Broker orders sent: zero.

**Limits**: A manager shutdown, dependency-triggered (nonmanual) target
stop, SIGKILL, system failure, service crashes and broker disconnects
are not prevented. This manual stop-request shield is a concrete
defense against repeating accidental operator/target stop, **not**
an independent Broker-native protective stop.
The versioned unit sources also remove `PartOf` for future releases,
but this live drop-in install deliberately does not claim that the
existing symlinked release source dependency was removed.

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


## Serialize guard installation with the existing maintenance latch

The installer now holds the same owner-private operator-maintenance.lock as
paper-on and maintenance-release for the entire unit-policy transaction,
including the initial observation, verification, receipt and failure rollback.
Previously a second installer could accept a first installer's guard files,
then lose them when the first installer later rolled back. The shared lock
serializes these cooperative operations without adding a permit file or a
new approval step. Both CLI entry points resolve the owner's home from the
passwd database, so different HOME values cannot select different locks.

This change only coordinates existing local tools. It does not authorize a
driver stop, release an existing hold, or add native execution capabilities.
