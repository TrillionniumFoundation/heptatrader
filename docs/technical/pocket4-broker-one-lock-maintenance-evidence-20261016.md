# Pocket4 original IB PAPER: coherent Broker maintenance observation

2026-10-16. P0 #331 source-only follow-on to native owner account-fence
PR #390, cross-client order identity PR #391, and original coordinator
one-lock local drain PR #393. No new IPC endpoint, order owner, trading
service, Broker connection, or extra approval is created.

**Observed bug:** user PAPER system.get_health independently acquired the
original IB adapter lock for authoritative risk, for account-global active
orders and again for the terminal download. EReader updates can change the
Broker epoch, active order identity, exposure and risk reconciliation between
these samples, fabricating a combination that never existed at the venue.
It also separately read connection and event-stream authority.

**Repair:** the original adapter's existing
GetAuthoritativeRecoveryAuditSnapshot() now copies transport-connected and
event-stream-authoritative bits under the SAME adapter lock as full active,
terminal, position, risk and exposure generations. User PAPER health consumes
that single native audit sample; mutation_ready no longer trusts separately
sampled active/risk or an unabsorbed post-fill generation. A minimal pure
diagnostic checks complete same-epoch snapshots, real recovered terminal
barrier, current exposure generations, finite physical/FX positions
(**strict numeric zero**, no rounding away small nonzero quantities) and exact
IB API client identity for every account-wide active numeric order ID.
Foreign API-client orders remain visible and cannot become original-client
correlation authority; an unknown client ID or missing mapping fails closed.

IB's official TWS API docs also clarify that reqAllOpenOrders does NOT
bind manually entered TWS orders: before binding they can use API orderId 0,
while permId is the Broker's account-unique identifier. This diagnostic
rejects orderId <= 0 as incomplete client-specific identity; it never
silently treats an unbound TWS order as absent, and does NOT connect client
ID 0 to bind/recreate it (which could affect exchange queue priority).
See https://www.interactivebrokers.com/docs/tws-api/doc/orders/modifying-orders
and https://www.interactivebrokers.com/docs/tws-api/ref/order .

The read-only response adds fixed-name, bounded account_broker_* fields:
one-lock coherence, zero observed, active/foreign order counts, and exact
reasonCode. If the full Broker sample is incoherent, numeric counts are JSON
null (UNKNOWN), not a fabricated zero. It NEVER prints account numbers or
Broker credentials.

**Important authority boundary:** Even broker_zero_observed=true means only
an original adapter-lock OBSERVATION at one callback processing point. IB
reqAllOpenOrders + terminal + positions are not one atomic external Broker
transaction, and this diagnostic has NO provable post-fence refresh watermark.
The health response explicitly emits
account_broker_sample_scope=ORIGINAL_ADAPTER_MUTEX_NOT_POST_FENCE,
account_broker_post_fence_refresh_proven=false and preserves
maintenance_stop_permitted=false. It cannot be used to stop an exposed
Execution, Gateway, FX or BTC Driver or to release the existing kill-switch.

Isolated assert-enabled fixtures on Pocket4 test real adapter connect /
disconnect provenance and strict negative evidence for different Broker
epochs, incomplete snapshots, changed exposure, pending fills, foreign
orders, missing client IDs, nonfinite positions and nonzero exposure.
No real Broker order/cancel, production unit restart or service cutover
is performed.

**Remaining P0 #331**: fresh post-account-fence Broker-global collection
that is causally bound to the original durable native fence and in-flight
send drain, cross-client/manual TWS completeness proof, and a Broker-native
independent protective exit surviving Driver or host loss. Only after
those are independently proven should controlled owner rollover be
considered. This is neither a new stop-permit nor a production rollout.
