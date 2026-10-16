# Pocket4 durable maintenance marker as native account-wide admission protection

The existing user CLI maintenance-hold writes an owner-private fsynced
operator-maintenance/maintenance-hold.json file and engages the original
volatile /run/user/UID/heptatrader/control/kill-switch.
Previously the original C++ Execution runtime read only the transient marker:
on reboot or /run/user reconstruction the durable operator hold survived but
native Broker risk ingress could interpret the absent transient marker
as disarmed. The Python paper-on guard alone does not protect direct native
Broker ingress from every session.

The original IbPaperKillSwitch now reads that same EXISTING durable operator
marker when the volatile marker is absent. No new authority owner, service
or trading route is created. Only the genuine, authenticated user PAPER
control directory enables the extra read. The trusted pathname comes from
the passwd database, never HOME/XDG or a remote request. Root/service
profiles retain their original behavior.

A private 0700 directory and private single-link 0600 marker engage the
native risk guard regardless of payload contents. Corrupt or partial
payloads do not silently reopen trade. Unsafe/missing directories, symlinks,
hardlinks, unexpected owner/mode or races return Uncertain (fail closed).
Original volatile marker always takes priority. Existing CLI release governs
the private durable hold. A restart with durable hold intact while the
/run marker is lost cannot open a new risk through native PAPER entry.

Scope: account-wide native risk ADMISSION hold shared by all sessions using
the original single IB Execution process. This does NOT establish atomic
Broker dispatch drain, same-account global order status across other
IB client IDs, an owner-authorized maintenance STOP permit, or a Broker
native protective STP/OCA surviving Driver death. P0 issue #331 stays open.
This source patch neither sends an order nor restarts resident trading units.
