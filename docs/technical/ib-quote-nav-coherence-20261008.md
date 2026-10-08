# IBKR PAPER FX quote recovery and NAV coherence

## Current owner and acceptance scope

- The mixed user scope has BTC.USD as the primary PAXOS contract and EUR.USD as a secondary IDEALPRO/CASH market-data contract. Reconnecting the execution owner, restarting a trading driver, extending quote lifetime or writing an order are **not** market-data recovery mechanisms.
- The Execution adapter loop may refresh only an out-of-date **secondary CASH** ticker after a 15-second minimum request interval. The refresh changes to a new broker request ID, cancels only the previous market-data request, and preserves the primary ticker and trading authority. Old-ID callbacks are ignored; an instrument is untradeable until real BID and ASK callbacks arrive on the new request, in the same broker connection epoch. Broker API failure is fatal and fails closed. No trading-order send/cancel is added by this change.
- The pre-existing 5-second quote freshness rule stays unchanged. No cached quote leg is re-stamped.

## NAV: two separate proofs, never conflated

1. **Transport-cohort proof:** `reqAccountUpdatesMulti` ledger and full-account subscriptions plus the independent positions request all signal `End` under unique request IDs, exact account/client, and one live connection. The collector records callback receipt windows and per-request completion times, rejects inconsistent receipt windows, retains the immutable original account rows, and never promotes rejected raw to accepted NAV. Each WATCH cycle uses new request IDs on a persistent, read-only API connection. There is no repeated handshake on the healthy connection.
2. **Financial reconciliation proof:** BASE ledger NAV agrees with aggregate `NetLiquidation` and its own components; individually reported currency balances, accruals and current-rate non-accrual components reconcile within the **unchanged USD 1.00** threshold. Failed arithmetic remains `ACCOUNT_NAV_FX_RECONCILIATION_FAILED`; account PnL stays `null` until other statement and liability evidence is independently proven.

**Hard limitation:** IBKR `accountUpdateMultiEnd` confirms receipt of the initial update set, not an atomic common valuation timestamp. Local callback arrival times do not establish the Broker's underlying FX mark time. `capture_cohort.broker_valuation_tick_attested=false` is mandatory; this evidence is not described as a same-tick broker valuation certificate. A true common mark requires independent Broker source data that names the same valuation time/basis. Do not manufacture a mark from a market-data quote or loosen the USD 1 bound to force a positive result.

## Regression requirements

- Quote recovery: per-instrument refresh only after the cooldown, same connection epoch and appropriate stale state; wrong epoch/primary instrument refusal; distinct replacement request IDs; old tick ignored; primary BTC subscription unchanged; new BID and ASK required to restore the FX quote.
- NAV: reuse one read-only session over multiple cycles; each cycle has different request IDs and a complete bounded download; all callback timestamps lie inside its receipt barrier; forged common-tick attestation is rejected; unresolved cross-currency differences still fail under USD 1.
- Deployment remains a separate qualification: pinned real IB SDK build, all relevant native/Python regressions, a broker-order and ownership check before maintenance, and real repeated observations of the new release. Passing pure unit tests alone is not a release certificate.

## Mandatory current receipt and historical replay

The observer now emits `heptatrader.account-download.v2`, in which `capture_cohort`
is mandatory. Dropping its end/arrival timestamps or claiming an atomic Broker
valuation tick fails validation, rather than silently falling back to the
legacy no-cohort path. Older v1 receipts remain verifiable for historical
reset-opening NAV; they do **not** attest a same-tick Broker valuation. Neither
schema is an external Activity/Flex confirmation or a substitute for a
Broker-issued valuation timestamp.
