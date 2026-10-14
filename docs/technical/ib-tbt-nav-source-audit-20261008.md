# IB PAPER quote-time / account-valuation-time source audit — 2026-10-08

> Historical running notes. Current candidate status and corrected independent-source
> interpretation: [PR #178 consolidation](pr178-candidate-consolidation-20261008.md).
> Earlier zero-callback windows do not negate later positive TBT receipts.


## Scope and immutable separation

This is a **read-only, non-authorizing** source-provenance experiment. It
cannot publish NAV, renew or alter a production Execution subscription, place
or cancel an order, modify a risk gate, or substitute market data for a
Broker account valuation. The 5-second dual-leg FX quote freshness rule
and the USD 1.00 NAV reconciliation limit remain unchanged.

The original account v3 observer collects an exact-account ledger, aggregate
account record and positions using three unique request IDs and independent
End barriers. This proves transport completeness, **not** that the Broker
valued every currency, FX conversion rate and BASE at one underlying tick.
Always retain broker_valuation_tick_attested=false absent separate
independently verifiable source evidence.

## Real TWS API source-timed Tick-by-Tick attempts

IB's Tick-by-Tick BidAsk callback has a Broker tick timestamp and **both**
bid and ask prices in one event. This would be useful as a source-timed
*market quote* if the Broker actually delivered that feed; it does not
certify the account's reported BASE NAV or interest/FX conversion basis.

Independent isolated client 29756 sent exactly one pinned
EUR/CASH/IDEALPRO/USD BidAsk subscription in each of two 28-second windows,
separated by a 20-second reconnect delay. Both sessions received the scoped
CASH-farm readiness message. The exact permitted socket opcodes were
startApi=71 (1), marketDataType(1)=59 (1), request TBT=97 (1),
cancel own ticker=98 (1). Each had 0 requested-ticker errors, **0**
tickByTickBidAsk callbacks, and 0 broker-order sends.

The independent MidPoint control (client 29757) used the same EUR/CASH
contract, CASH-farm precondition, and exact market-data-only wire. One
22-second window likewise returned **0** tickByTickMidPoint callbacks and
0 request-specific errors. A MidPoint response, even if available, is not
a BID or ASK leg and must never authorize trading.

Reproducible, locally private, non-NAV machine receipts:

- /tmp/heptatrader-tbt-bidask-real-20261008.jsonl
  SHA256 e5098160d9cc0e9e43ff374bd278a71ea31e02defb4215ccba4771b4aa36c4f3
- /tmp/heptatrader-tbt-midpoint-control-20261008.jsonl
  SHA256 db0bf24f624b2d16c0e6e4097721f10efd909099b3268c9d3bba7c5d3f1d8b0e

**Disposition: TBT source not qualified.** No negative Broker error was
observed, so the exact entitlement/routing/source limitation is unknown;
these samples do not prove permanent lack of support. However absence of a
single timestamped event forbids using TBT as the production quote source.
Do not repeat the same experiment blindly or bypass the dedicated
subscription-line limit / same-instrument minimum re-request interval.
Operator should request IB confirmation of TBT FX availability / entitlement
or obtain a specific new reason for a controlled retest first.

The production alternative remains the separate native reqMktData
secondary-CASH refresh candidate. It relies solely on **real positive,
uncrossed, current-request/connection-epoch BID and ASK callbacks**. It
must not re-stamp an old ASK if only BID updates. Successful isolated
standard FX quotes cannot substitute for the candidate's controlled
real farm loss/recovery/reconnect and nonduplicated-order qualification.

## Account valuation evidence classification

| Evidence | What it certifies | What it cannot certify |
| --- | --- | --- |
| TWS reqAccountUpdatesMulti three End callbacks | All three current requests finished on the read-only connection | Same Broker source valuation time or coherent per-currency FX mark |
| Local callback timestamps / per-currency request IDs | Consumer receipt times and transport ownership | Source valuation clock |
| Web API portfolio ledger / ssd timestamp | Per-value **retrieval** time, according to IB documentation | Atomic cross-currency and BASE valuation time |
| Real-time reqMktData or Tick-by-Tick | A market **quote** or tick at its own time | IB's historical / accrued-interest basis, or same-time account NAV |
| IB daily Activity / Flex report | Account's official **period-close reporting** NAV and daily conversion-rate basis, if its source, dates, per-currency totals and components are complete | The 10-second intraday TWS NAV snapshot or an exact common underlying market tick |

A useful *separate* EOD anchor is an official IB Activity/Flex statement
that includes **Net Asset Value (NAV) in Base Currency**, the account's
currency cash / accrual components, **Daily Conversion Rates / Base Currency
Exchange Rate** and report date. IB documents conversion rates as of the
report date with Reuters closing midpoint near 4 p.m. ET. Treat these
as the Broker's *defined close-of-period reporting basis*, not a proof that
every tradeable asset and FX leg was marked at one instantaneous clock tick.

The official daily reporting source could independently cross-check
the Broker's date-scoped EOD values and commissions/flows. Only a
complete authenticated statement covering this exact PAPER account and date,
with source fingerprint, unambiguous currency coverage, report currency
and unchanged numeric tolerances, can be considered. A current intraday
NAV observation must **not** borrow a prior date's close or the statement's
REST retrieval timestamp.

Current pocket4 has no available imported Activity/Flex XML statement
in the NAV state root and no configured Client Portal Web API listener. It
therefore has no complete Broker-issued statement available for source
qualification. The deployed observer correctly leaves rejected-raw
unaccepted and NAV authoritative=false.

## Strict remaining gates

1. Require a newly justified Broker Tick-by-Tick diagnostic only if IB
   confirms service availability/entitlement or another concrete cause
   for the zero-callback behavior is established. No TBT production cutover.
2. Complete the **native candidate**'s real farm-down/up, first valid
   dual-leg FX replay, reconnect and sustained quote freshness tests.
   In the same trial check original OMS command IDs, FX/BTC positions,
   orders, journal counters and no duplicated sends. No restart outside
   an approved safe maintenance window.
3. For NAV, acquire genuinely source-aligned Broker account evidence
   with per-currency and BASE valuation basis or retain the current
   strictly rejected state. For EOD, compare official period-close
   statement separately, never relabel it as the current 10-second NAV.
