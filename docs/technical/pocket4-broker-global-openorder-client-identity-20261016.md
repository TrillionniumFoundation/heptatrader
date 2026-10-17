# IB reqAllOpenOrders client-scoped numeric identity repair

P0 #331: The original native Execution adapter obtains all-API-client
active orders using reqAllOpenOrders(), but the SDK's numeric order ID is
client-scoped. A different API client on the SAME Broker account can use
the exact same numeric ID as a local order.

The original adapter previously fed foreign openOrder events into its
own-client cancellation lifecycle, and stored global orders in a
set<long> with no client identity. A local numeric terminal could erase a
foreign active order and silently invent a zero-global-order projection.

The existing adapter now keeps a companion orderId => clientId map in
the authoritative active snapshot, requires a valid client ID for every
account-scoped callback, and invalidates the whole snapshot when two API
clients reuse one numeric ID. Foreign client active orders remain counted,
but never become own-client cancellation/terminal or H1 correlation proof.
Incremental callbacks use the same collision check; a local terminal
cannot retire an active foreign-client order based on numeric ID alone.
Unknown SDK client IDs fail closed. Full native refresh with openOrderEnd
is required to remove a foreign order that has disappeared.

This does NOT make IB reqAllOpenOrders a transactionally atomic view of
every manual TWS or external system order. Global Broker stop admission,
unknown-send reconciliation and independent broker-native stop/OCA still
require separate proofs. A successful client-scoped order read is NOT a
maintenance stop permit. Source-only; never restart live Trading Drivers
to activate this patch without owner continuity verification.

The tests exercise real native adapter callbacks in a PAPER fixture,
including cross-client duplicate IDs, inverted callback order, unknown
client identity, late callbacks, no foreign cancel dispatch and
non-retirement by our same-number local terminal. No live orders sent.
