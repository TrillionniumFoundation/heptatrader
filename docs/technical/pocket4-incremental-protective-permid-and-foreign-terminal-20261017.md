# Pocket4 original IB callback identity and read-only protective metadata

2026-10-17. P0 #331 follow-up to original manual TWS permId and Broker
protective-order candidate census. This patch changes no broker credentials,
trading authority, permissions, service topology or live risk limits.

## Concrete native correctness gaps

The original full reqAllOpenOrders + OpenOrderEnd refresh retained
protective candidates keyed by positive Host-wide permId. Subsequent
incremental OpenOrder callbacks already merged native active numeric IDs
and manual unbound permIds into the COMPLETE active snapshot, but DID NOT
update its protective metadata. This created an inconsistent generation:
a new visible stop could be counted as active but missing from the
protective candidate census, while changed/removed stop parameters could
leave a stale candidate until the next global refresh.

The original local terminal retirement removed its native client numeric
order ID and associated permId but did not remove the corresponding
protective candidate; a once-visible stop could remain in the read-only
candidate census after cancellation.

Broker 201/202 error callbacks use an API-client-scoped numeric order ID.
An error explicitly identifying a different client or other account
cannot be used as the ORIGINAL owner local order lifecycle/terminal
acknowledgement even if its orderId collides with ours. A known foreign
order's native terminal or 201/202 error also INVALIDATES the old complete
global order/protective-candidate census; neither retaining the stale
candidate nor auto-cancelling it is valid Broker evidence. Unknown/unscoped
errors remain diagnostic and are NOT general Broker-account terminal proof.

## Minimal repair

The original IB adapter now uses a shared candidate-shape/identity
comparison for complete and incremental callbacks. After a successful
native incremental identity merge, the exact same account/epoch callback
updates protective candidates by positive permId. An exact duplicate is
idempotent. A changed protective candidate or a change from protective
to ordinary requires a fresh full global Broker read; the completed
snapshot is INVALIDATED rather than silently replacing evidence. A
candidate without a positive permId sets sticky
protectiveMetadataIncomplete. Foreign and unbound manual TWS candidates
are visible but never become local cancellation authority.

The original native local terminal erases matching candidate permIds
together with the exact local active ownership. Foreign candidate permIds
cannot be retired by local numeric terminal. Broker errors with
positively known foreign client or other account identity cannot
produce local terminal state.

## Still NOT a Broker-native independent protection proof

IB openOrder metadata is only a candidate. It does not prove Broker
acceptance, venue/native trigger ownership, right-side and exact coverage,
OCA handling, partial-fill behavior or persistence after Driver/Gateway
loss. In PAPER, IBKR warns stops and other complex types are simulated.

Official refs:
https://www.interactivebrokers.com/docs/tws-api/ref/order
https://www.interactivebrokers.com/en/trading/tws.php

All user PAPER health independent stop survival fields continue to be
hard false. No live stop order, cancel, new client ID 0, Broker login,
service restart or exchange interaction is performed. This patch may
improve the visible candidate census; it is not a permission to stop a
protected service. Remaining issue #331 stays OPEN until independent
protective Broker-order existence/survivability is proven outside the
original Driver with full account-scope identity.
