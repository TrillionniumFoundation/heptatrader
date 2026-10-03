# Incomplete historical download with an original pending order

Status: implemented; not an order-terminal or trading-qualification certificate.

## Failure reproduced

A connected Gateway can return openOrderEnd, execDetailsEnd and positionEnd but
omit completedOrdersEnd. The production startup previously waited for terminal
history before requesting its account/position recovery snapshot, then exited
on the readiness deadline. A resident service repeatedly restarted and lost the
original client's callback continuity. An independent raw SDK reproduced the
missing End for both account-wide and API-only historical requests.

## Native recovery behavior

For explicit user PAPER scope only, a complete same-epoch active-order snapshot
whose IDs are fully correlation-bound can retain the connection when the sole
historical error is IB_TERMINAL_CORRELATION_REFRESH_PENDING. The process latches
startup_recovery_read_only. It exposes original order/status reads and requests
an informational account/positions refresh, without claiming a recovery barrier.

No empty terminal set is invented. No cash restart checkpoint is advanced.
Place and flatten final-send gates reject; cancel preflight and final dispatch
reject. Policy risk/flatten snapshots cannot issue valid trade previews. The
health response states mutation_ready=false, startup_recovery_read_only=true,
and the actual terminal_download_complete value. The flag cannot automatically
unlatch when a late callback arrives; another fully proven startup is required.

Original callbacks remain durably tied to their original command and correlation.
The read-only process does not fabricate a pre-fill baseline, release order
ownership before late execution evidence, reconcile against incomplete history,
or resend completed-orders requests whose End has no request ID. It makes
bounded open-order refreshes while no post-fill reconciliation is pending.

## Acceptance boundary

The native helper has negative tests for wrong scope, transport loss, stream
loss, zero/mismatched epochs and generations, unbound/duplicate active IDs,
empty active sets, completed history and conflicting historical errors.
Production targets build with the real IB API; the native CTest suite passed
49/49 during this change. This restores a usable recovery receiver instead of
a restart storm; it does not claim to make a Broker PendingCancel order final.
Crypto 24/7 product availability does not establish this PAPER account's
Basic/Plus entitlement or explain a missing destination cancellation ACK.
