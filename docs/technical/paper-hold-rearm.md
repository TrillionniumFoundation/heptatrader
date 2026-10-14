# PAPER native hold rearm — user-scope safety candidate

## Reason and scope
The live Pocket4 paper driver units are both persistently masked. In addition,
the native PAPER kill-switch marker under /run/user/1000/heptatrader/control
was engaged and independently read as IB_PAPER_KILL_SWITCH_ENGAGED.
That marker lives in runtime tmpfs and therefore does not survive reboot.
Auto-driver masks alone do not prove every manual risk-increasing gateway
is disabled. Risk-increasing admission checks the native marker, while
cancel and authoritative reduce-only/flatten use separate owner checks.

## Reviewed startup helper
scripts/hepta_paper_hold_rearm.py is a stdlib-only, fail-closed, user-scope
ExecStartPre candidate. The CLI accepts no path, account or token arguments,
never sends broker requests, never alters a service or disarms the switch.
It opens the exact runtime user directory with NOFOLLOW and pins its owner
and 0700 mode; it verifies the heptatrader parent. It only creates an absent
control child with UID/GID ownership, 0750 mode and link count two, and an
absent native marker with mode 0440 and link count one on the same device.
An already present unsafe directory/marker is refused, never silently
repaired. This also refuses symbolic links, hard links, FIFOs, wrong modes
and an absent higher-level runtime user parent.

## Operator deployment and qualification
Do not replace or restart the original resident Execution just to install
this guard. A future preflight drop-in must preserve the exact existing
ExecStart override, sockets, environment file, and two persistent masks.
A separately tested transient user systemd unit may exercise ExecStartPre
without touching current Execution. Daemon-reload cannot prove real reboot
ordering. An actual boot test is a separately gated maintenance experiment;
unsafe failure may block Execution startup, so the owner must preserve
independent manual broker recovery.

Removing the deployed helper and disarming the native marker is an explicit
operator-release action after owner identity, broker portfolio state, NAV
and execution safety have been independently accepted. This helper has no
disarm API. A passing helper test never authorizes new PAPER or LIVE orders.

## Pocket4 observed installation, 2026-10-14

The helper was installed as a source-hash-verified private 0500 script under
the operator-guard release directory. Its SHA-256 is
c4d01958dec7a8f448db40d2f2221aea89df71917526a58d1bc841fe7ad0cc7d.
14 isolated filesystem tests passed, including symlink, FIFO, hard-link,
directory link count, wrong mode and idempotent/reentrant inspections.
A separate one-shot user systemd unit successfully executed the exact script
as ExecStartPre and observed an already legitimate engaged marker.

An additive 95-paper-hold-rearm-20261014.conf drop-in was then loaded into
the resident Execution user service, leaving the existing immutable executable
override untouched. Only daemon-reload was performed; PID 1824767 and the
original resident executable remained unchanged, both autonomous drivers
remained masked, and the native marker remained 0440/UID-GID 1000.
The existing drop-in directory had group-writable mode 0775, so it was
narrowed to 0755 before attaching the new entry. No new trade was submitted.

This is a protective boot-preflight configuration, not proof of an actual
reboot. The next boot has not been exercised with the open FX position, and
the risk-increase/reduce-only operational distinction remains mandatory.
Future boot-path failure deliberately refuses Execution startup rather than
running with a missing native marker; the operator must preserve manual
broker recovery. Operator receipt is private on Pocket4, not published here.

The R3 held native binary remains separately pinned to R3 commit
3dc44894b345ab576f14e453bb1ca7084a7f0e50. The R4 branch adds only
this safety script, tests and documentation; do not relabel the R3 native
artifact as a byte-reproduced full R4 source release.
