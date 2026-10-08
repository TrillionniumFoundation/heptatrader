# Account NAV: same-socket live FX witness

## Why the original rejection is real

The exact-account IB reqAccountUpdatesMulti callback publishes per-currency
`ExchangeRate` values and BASE `NetLiquidation` which may not share a
valuation timestamp. For PAPER EUR.USD with 89,388 EUR short, a previously
accepted download reconciled within cents; later USD BASE NLV moved tens of
dollars while the downloaded EUR exchange-rate field remained unchanged.
Simply expanding the fixed USD 1.00 account-reconciliation limit would
manufacture NAV authority and is forbidden.

## Independent, source-timed alternate proof

The read-only, exact-account observer requests one real-time
`EUR/CASH/IDEALPRO/USD` market-data ticker (request ID 4) **on the same IB
API socket** as its paired ledger and aggregate account reads. The outbound
boundary accepts only the pinned SDK's exact request wire for this ticker,
`reqMarketDataType(1)`, its own cancel and the original account/position
subscriptions; broker order sends remain disallowed.

The observer requires the Broker's `marketDataType(4,1)` callback and real
BID=1 and ASK=2 tickPrice fields. It captures the currently observed live
BID/ASK, their individual local callback timestamps, and the exact account's
`NetLiquidation` callback value/timestamp at that callback. Later quote
callbacks are never retroactively applied. Errors and any subsequent change
to a non-live data type invalidate the alternate witness.

The validator accepts an alternate comparison **only** if the existing
currency-rate comparison fails, and only if all these independently
verifiable facts hold:

* Same account/client/connection identifier, PAPER period and IB request
  witness; exact account NetLiquidation callback value and timestamp;
* both positive live bid/ask callbacks occurred before the aggregate NLV
  callback, not more than 5,000 ms earlier; the required paired ledger
  callback fields are within 1,000 ms of that NLV callback;
* EUR is the only materially exposed non-USD currency without its own
  quote witness; non-EUR noninterest exposures are at most USD 0.01;
* positive uncrossed EUR bid/ask and bid/ask spread at most 0.0001 USD/EUR;
* currency-unit noninterest, CashBalance and NetDividend translated at
  this exact witness **midpoint** independently match the Broker BASE
  fields within the **unchanged USD 1.00 absolute bound**; aggregate
  NetLiquidation matches BASE net liquidation under the original bound.

Actual FX income accruals remain based on the Broker BASE AccruedCash
without double-counting currency current-rate interest. The stale
`ExchangeRate` and its disagreement stay in the normalized result as
diagnostics; the alternate proof, quote ages, source digest and strict
acceptance basis are separately recorded. An unaudited implied rate
algebraically derived from BASE balance never authorizes NAV.

If the full exact raw download disagrees after this proof, fail closed.
The collector can repeat this **only** for FX reconciliation failure,
up to three independent read-only acquisitions of the same period.
It never carries a price across observations, widens tolerances, modifies
a position/order or alters FX/BTC execution authority.

## Deployment qualification

Offline tests cannot prove a live account's future quote/NLV alignment.
Before deployment, use the pinned IB SDK, preserve existing observer
and PnL state, and compare accepted/rejected raw sha256 against
normalized NAV with `validate_nav_evidence`; observe repeated
successes and intentional failed alignment. No `account_nav_complete`
or account-wide return claim is valid without period opening NAV,
dated activity/Flex cash flows, posted charges and external liability
coverage. If IB quote rights or same-time evidence are absent, NAV stays
UNKNOWN. The FX secondary-quote renewal patch has its own controlled
Execution restart and order/position recovery gates.
