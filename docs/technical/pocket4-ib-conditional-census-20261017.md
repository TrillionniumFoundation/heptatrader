# Pocket4 IB global conditional trigger census (2026-10-17)

**Read-only original Adapter correction.** This is a source fix, not a
Broker protective stop, new order type admission, exit authorization, model
promotion, or safe-to-stop-Driver certificate.

## Observed gap and actual failing fixture

The original `IsIbVisibleProtectiveCandidate` in
`HeptaTrade/adapter_ib/ib_gateway_adapter_event_state.cpp` treated only
STP, STP LMT, TRAIL and TRAIL LIMIT (plus nonzero Parent/OCA) as
potentially conditional. IBKR officially documents `MIT` and `LIT` for
CASH/forex and `STP PRT` for futures. A real account-wide
`openOrder(Submitted)` may have such an order **without** Parent or OCA.
These types may open new risk as well as close existing risk: the
diagnostic must never call them proven protective stops.

A new negative source-bound C++ Adapter fixture uses the existing
original IBAPI-enabled Adapter, with a separate foreign Broker client,
same exact account and a native SDK Order and Lite event from the **same**
original callback. Test-only first commit `d5b7177` was actually built
on Pocket4 with IB SDK 1030.01; `hepta_ib_live_terminal_reconciliation_tests`
**aborted at the newly introduced positive visibility assertion** because
the native order failed to enter the global conditional candidate census.
The red test sent **zero** Broker orders, cancels or account mutations.

The fixture then requires, for each MIT/LIT/STP PRT type:
- Valid matching Native/Lite callback + positive Broker permId is in
  the global candidate census (never granted stop-working authority).
- A Lite projection forged as ordinary MKT while the actual native SDK
  Order retains the conditional type invalidates the full Broker order
  snapshot in both full-download and incremental paths.
- Existing ordinary MKT with neither parent nor OCA stays ordinary.
- Foreign-client conditional orders remain globally visible but never
  establish the original H1 owner identity.

## Bounded fix and release semantics

A single private `IsIbConditionalTriggerType` helper adds
`MIT`, `LIT`, and `STP PRT` to the existing original known conditional
types. The original account-wide and incremental visible candidate
decisions, and native-vs-Lite same-event mismatch check, use the **same**
predicate. Original quantity, Broker permId, client, account, Parent/OCA,
trigger and send semantics are unchanged. The check is conservative and
may refuse a contradictory Broker download until a genuine coherent
refresh; it does **not** create a new risk-accepting path.

**No existing Execution binary, Broker Gateway/client, OMS event, native
or persistent new-risk hold, FX/BTC strategy service or model is changed
by this research/source test.** The resident original Executor remains
on its pinned binary. This does not by itself qualify independent stop
coverage during Driver absence (P0 #331).

Official references:
https://www.interactivebrokers.com/campus/?p=195739&post_type=ibkr-api-page
https://www.interactivebrokers.com/docs/tws-api/ref/order
