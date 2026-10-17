# Original Pocket4 IB Broker protective-order metadata observation

P0 #331, 2026-10-17. The original resident trading service's Broker
account-wide OpenOrder callback exposes Host permId, client/order id, order
type, auxPrice, OCA group, parent order ID and transmit bit. Previously
these protective-order details were discarded before read-only audit.
The original adapter now retains them as a generation-scoped map keyed by
positive Broker permId, and clears it on disconnect, incomplete read, failed
callback or a newer successfully completed global order download.

This is purely read-only: no new connector, no clientId=0, no order binding,
no order create/cancel/modify, no new native trading controls. A callback
with a STOP / STP LMT / TRAIL or OCA / parent relation is only a VISIBLE
candidate. Conflicting same-permId candidate metadata invalidates the
original account-wide snapshot. Missing permId invalidates metadata
completeness; neither condition may be misinterpreted as Broker protection.

The original user PAPER health exposes bounded counts and
account_broker_independent_stop_survival_proven=false UNCONDITIONALLY.
A visible STP or OCA order is NOT evidence of:
* Correct risk-reducing side and size relative to every actual position.
* Exact parent-child activation, full transmission, venue acceptance.
* OCA group behavior, handling of partial fills or Broker routing outages.
* Broker-side persistence after original Execution/Gateway/Driver exit.
* Complete protection across FX CASH and crypto contracts or manual orders.
* Atomic Broker-account-wide stop of independent API clients.

The exact account-protective survival gate remains failed until controlled
Broker-native independent protective stops are proven to survive owner
shutdown in a safe, separate venue-supported PAPER test with original
owner continuity and Broker authority. The present task never runs such
a live test. Keep P0 #331 OPEN; never alter RefuseManualStop, original
kill-switch or persistent user operator hold based on this census.
