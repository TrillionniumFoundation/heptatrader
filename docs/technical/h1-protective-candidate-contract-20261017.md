# Pocket4 — H1 Broker protective candidate shape integrity

2026-10-17 PDT. **Research-only source change; no Broker order authority.**
Stacked on original-owner H1 protective census research (#411), NOT a
production Execution deployment or a Driver-stop permission.

## Bug: a changed protective instrument/OCA left a complete census

The original global `reqAllOpenOrders` candidate ledger keys a visible
protective order by its positive Broker `permId`. A subsequent incremental
`openOrder` for the same account/permId compared order type, action, OCA
group, parent numeric ID, transmit, aux price, quantity, client and numeric
order ID, **but NOT the Contract or SDK-native OCA/TIF/trigger fields**.

An original CASH EUR/USD STP could therefore retain a `complete=true`
global candidate snapshot even after a later callback changed its
symbol/conId, OCA type 1 -> 2, GTC -> DAY, or trigger method. IBKR's
TWS API differentiates OCA 1 (cancel remaining, with block) from OCA 2
(proportional reduction, with block); it is not a cosmetic field:
https://www.interactivebrokers.com/docs/tws-api/ref/order

The actual Pocket4 SDK-enabled C++ Adapter red test commit `ecba3d2`
used the existing foreign-client Broker callback fixture (no executable
venue). A fixed positive `permId` followed by a changed Contract or
native OCA setting still reported a complete candidate; the test aborted
at the expected-incomplete assertion. Original source did **not** meet
the stated invariants.

## Bounded fix and negative controls

In `IBVisibleProtectiveOrderCandidate` and the original, read-only
`MakeIbVisibleProtectiveCandidate/SameIbVisibleProtectiveCandidate`
codepath, retain/compare:

- visible Contract symbol, security type, currency and exchange;
- original `orderRef` and limit price;
- SDK-native presence and exact conId, OCA type, time-in-force, and
  stop trigger method, when the native SDK fields were actually present.

Changed metadata on the same `permId` now causes
`IB_PROTECTIVE_INCREMENTAL_CONFLICT_REQUIRES_REFRESH`, invalidating
the current *entire* authoritative correlation generation and clearing
candidate evidence. Exact duplicate callbacks remain idempotent.
Eight adversarial variations include changed contract symbol, conId,
OCA type, GTC/DAY, trigger method, limit price, original orderRef and
missing native SDK evidence. The fix is deliberately observational;
it neither creates a Broker working stop nor authorizes order cancel.

A callback lacking original native SDK data is not allowed to invent
the missing fields. Any changed native-data presence under the same
positive permId must be independently re-established via a new full
Broker download. The original global Broker account/client/permId
scope and H1 exact-parent/STOP proof are preserved.

## Actual Pocket4 verification

All work was performed on Pocket4 in an **isolated detached worktree**
and an independent Release/Ninja build with original IB SDK 1030.01,
GNU C++ and system CMake 4.2.3. No new IB login or service restart.

- Before patch: actual native Adapter test **FAILED** at its new
  expected-incomplete assertion.
- After patch: actual native Adapter test **PASSED**; 8 variations +
  byte-identical duplicate tested.
- **21/21 selected native CTests PASSED**, including original
  Execution Coordinator, H1/OMS/OCA/Adapter, PAPER risk, startup/quote
  and full synthetic runtime recovery (28 scenarios; zero Broker
  socket/order-method calls). Native test log SHA256
  `2abe76c2e96db52f7b09ee19a156c45175cc08b16b46c0d266d0894642776ea8`.
- **116/116 Python Source tests PASSED** with private TMPDIR.
  SHA256 `204e85bae761187f8e4c8a192f1e726fd824e95643ee84d916f9b203c7d2e70c`.
- Complete research `hepta-ib-executiond` binary linked in isolation,
  SHA256 `51ec6f4f52f83eb5d5d858ae5a28c5cdd43def70b737a6a7c1c04ef98e50fde2`.
- Private evidence root:
  `~/.local/state/heptatrader/operator-evidence/h1-protective-shape-20261017/`.

**Status:** source-only research, no model promotion, Broker order, order
cancel, new market data session, resident service cutover or maintenance
hold release. Original Pocket4 Execution PID, FX and BTC Drivers remain
unchanged; native and durable risk holds remain engaged.

Crucially `nativeStopBrokerWorkingProven`, `maySendOrdinaryExit`,
`mayStopDriver` and `maintenance_stop_permitted` remain FALSE.
IBKR PAPER complex stops may be simulated; this fix is NOT a broker-side
independent protection survival witness. P0 #331 remains OPEN.
