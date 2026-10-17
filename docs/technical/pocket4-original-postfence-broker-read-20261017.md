# Pocket4 original-owner post-fence Broker read (non-stop diagnostic)

2026-10-17. P0 #331 follows existing native account-wide FenceAccountNewEntries,
one-lock local drain, client-scoped Broker order identity and one-lock
original adapter health.

## Exact native authority and sequencing

No new Broker owner, IPC operation, API client, trading service, order/cancel
or stop permit. The authorized original user PAPER FenceAccountNewEntries
control remains the only start: configured Crypto agent/session/account/IB
venue/PAPER domain/zero recovery ingress must match. The original
coordinator first fsyncs its durable account no-new-entry fence.
The original callback checks ONE-lock local hot coordinator projection:
zero owned and unknown orders/uncertains and original Place/Flatten/Cancel
venue dispatches, plus the existing independent native kill-switch must
block new risk.

Only then does the original IB adapter issue ONE new reqAllOpenOrders()
on the original resident Broker client. Under its own original adapter
mutex it records connection epoch, active request generation, current
Broker mutation generation, economic exposure generation and old terminal
generation. Once a post-request OpenOrderEnd is handled, it dispatches
ONE coherent account+positions risk refresh, ordered AFTER active End.
Repeated system.get_health reads advance existing proof stages without
extra Broker request or reconnect.

Any disconnect, stream overflow, broker mutation, new economic fill,
active/terminal/risk generation drift or incomplete End invalidates the
diagnostic. The original Broker recovery barrier must agree.

Existing health gains bounded account_post_fence_* fields for started,
active End, risk End, active-then-risk observed, observed zero, exact
epoch/generations and reason. maintenance_stop_permitted and
account_post_fence_stop_permitted are BOTH unconditionally false.

## Real external Broker limits

OpenOrderEnd has no request ID, so its callback could in principle be a
late same-epoch End; the diagnostic does not claim unique Broker request
identity. The original completedOrdersEnd is also untagged and cannot
safely be reissued in the same epoch. Thus terminal state is earlier
than the durable account fence, and
account_post_fence_terminal_refreshed is ALWAYS false.
reqAllOpenOrders cannot establish a full inventory of unbound manual
TWS orders by global permId and cannot fence independent Broker clients.

Even a positive post-request Broker active+position snapshot is NOT a
transactional all-client zero, protective STP/OCA, safety STOP permit
or license to restart Execution or either PAPER Driver. P0 #331 remains
OPEN for new-epoch terminal evidence, external/manual order universe,
independent Broker protective stop and owner-controlled continuity.
Resident RefuseManualStop, native kill-switch and persistent user hold
remain in place. No canary Broker trades for this read-only diagnostic.

## Native acceptance

Offline original adapter fixtures verify request counts, strict order
of global OpenOrderEnd before coherent risk dispatch, no resend, foreign
active orders, unknown client fail-closed, revoked evidence on disconnect,
and no implied terminal-after-fence witness. Original policy tests verify
only authorized Crypto custodians trigger the callback AFTER fsync.
All artifacts/tests/failed evidence stay on Pocket4 only.
