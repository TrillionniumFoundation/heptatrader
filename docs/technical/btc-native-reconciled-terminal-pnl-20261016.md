# Pocket4 BTC native owner terminal accounting repair — 2026-10-16

The real original PAPER orders 281, 290 and 365 have durable place_sent identities, distinct commands and unique venue correlations. Their original orderStatus streams end at PendingCancel. The native Broker nevertheless recorded repeated completedOrder(Filled) callbacks, and an independent original Execution owner recorded one order_owner_reconciled_terminal for each original order.

The native completedOrder deliberately omits the connection-scoped numeric order ID and the instrument/side subject. It carries numeric order_id=0 and broker_client_id=0, with blank req_id. These sentinel fields are *not* proof of the original order ID or owner. Broker restart downloads can duplicate callbacks without creating new executions.

Read-only PnL replay now recognizes this narrow independent conjunction:
- The immutable original place_sent binds command, account, session, instrument, direction, numeric order ID and venue correlation.
- The original native owner reconciliation is independently scoped to that same account, PAPER domain, instrument, direction, session, order ID and venue correlation, with its *own* internally paired read-only request ID and ORDER_OWNER_RECONCILED_TERMINAL reason.
- A native completedOrder(Filled) callback has the exact original venue correlation, expected blank instrument/side, expected numeric sentinels, account/PAPER domain, valid service and connection epoch, original source/callback type and required Filled status.
- The prior validated full-subject original execDetails includes that same service and connection epoch, actual execution identity/price/quantity, and exact original venue correlation. The completed callback predates the owner reconciliation.
- An original orderStatus terminal always wins. An independently validated Broker error 202 wins before this route. A later nonterminal status rejects the new proof.

It does not infer a zero fill, borrow a numeric ID, fabricate financial amounts, issue Broker orders, mutate the OMS journal or override the native stop shield. Standalone completedOrder/owner assertion are insufficient. Deliberately malformed, foreign, mismatched-epoch, reordered, duplicate and subsequent-active-status fixtures must all fail or remain pending as appropriate.

Pocket4 canary validation must compare the same frozen journal SHA256 and period SHA256, all execution, fees, positions and PnL numerical values against the previous #377 PnL implementation. The ONLY permitted semantic differences are the three original pending IDs and the new bounded native-owner evidence witnesses. Deploy only as a distinct versioned, rollbackable **read-only PnL observer**; the Execution/FX/BTC Drivers retain their live process identities. This work is not an Execution-owner atomic maintenance fence and does not authorize a driver restart.
