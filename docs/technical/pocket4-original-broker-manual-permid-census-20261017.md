# Original Pocket4 IB Broker permanent order ID read-only census

2026-10-17. P0 #331. Fixes an identity/counting blind spot in the
original native Broker adapter and reuses the EXISTING original-owner
reqAllOpenOrders callback path. No new Broker API connection, no client ID
0, no order binding, no Broker order, cancel, or protective-order mutation.

## Broker identity

Interactive Brokers' official order API documents the following:
- orderId is API-client-scoped, while permId is assigned by TWS as a
  persistent account-wide Host order identifier;
- a manually created, UNBOUND TWS order is returned with API orderId=0;
  multiple unrelated manual orders can share numeric zero;
- reqAllOpenOrders does NOT bind manual orders; reqOpenOrders on clientId=0
  and reqAutoOpenOrders may bind, causing venue cancel/resubmit and possible
  loss of exchange queue priority.
- the whole Broker account's future manual/external order universe is NOT
  transactionally fenced by the original API client.

Official references:
https://www.interactivebrokers.com/docs/tws-api/doc/orders/modifying-orders
https://www.interactivebrokers.com/docs/tws-api/ref/order

## Original native implementation

The pinned IB C++ SDK openOrder and completedOrder callbacks carry
Order.permId; orderStatus carries the native permId argument and execDetails
carries Execution.permId. Original IBEvent retains a strictly positive
64-bit brokerPermId separately from client-scoped orderId.

The original adapter's complete/ongoing active-order snapshots now retain
each distinct positive permId -> (clientId, numeric orderId), and a
separate set of all unbound manual permIds where (clientId,orderId)=(0,0).
Two manual order callbacks with ID 0 and different valid permIds represent
TWO distinct active Broker risks, not one. They never become the original
Execution owner's cancelable order or H1 correlation.

An OpenOrder callback with EMPTY account identity cannot be safely scoped
to another account; the original active snapshot is invalidated rather
than silently treating the unknown Broker order as absent. Orders with
a positive, different account identifier stay out of scope.

Unknown/nonpositive permId for id=0, id=0 with nonzero client, and the
same positive permId supplied with conflicting client/numeric identities
invalidate the account-wide snapshot. Missing permId for a positive
nonzero API order is separately exposed as incomplete permId coverage,
not fabricated into a stable account ID. A later completed new Broker
download is required to remove unbound manual IDs; a local terminal or
numeric status cannot remove someone else's manual order.

Every relevant original active-order risk count uses visible distinct
manual permIds, not set<long> numeric keys. The original account health,
PAPER risk, recovery owner audit and post-fence read diagnostics surface
bounded aggregate counts, observed permId coverage and unbound manual
order counts only. They never print an account number or actual permId
through the new diagnostic metrics.

## Safety and remaining scope

Even zero visible manual order callbacks does NOT prove that the API client
can see all orders entered from every TWS username or external venue.
externalManualTwsUniverseComplete stays hard false. Original health and
post-fence diagnostics permanently deny STOP. The original existing
kill-switch and all protected resident units remain intact.

The isolated C++ tests use native openOrder callbacks with two different
Host permIds both reporting numeric orderId=0, a third incremental manual
order, unknown/missing identity, conflicting permId owners, original
PAPER risk and the next complete read. They assert NO PlaceOrder/Cancel
calls, no implicit order binding, missing account scope, original post-fence\nread not attesting zero with manual orders, and exact visible counts. All
worktrees, models, tests and logs reside ONLY on Pocket4.
