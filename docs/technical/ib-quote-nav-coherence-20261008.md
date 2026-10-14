# IBKR PAPER FX quote recovery and NAV coherence

> Historical running notes. Current candidate status and corrected independent-source
> interpretation: [PR #178 consolidation](pr178-candidate-consolidation-20261008.md).
> Earlier zero-callback windows do not negate later positive TBT receipts.


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

## Rejected-source diagnostics

When current FX/BASE translation fails, write `rejected-raw.json` and a
separate `rejected-fx-diagnostic.json` bound to its SHA256. The report tracks
original rate/BASE differences and callback timing without being an accepted
NAV, a substitute quote, a new order or a widened limit. Diagnostic algebraic
implied FX rates are circular and never qualify the original account mark.

Consecutive rejected downloads are compared to the previous rejected cohort
when its period matches and observation time advances. Unchanged reported FX
rates under a changing BASE aggregate are provenance diagnostics, **not** a
common Broker valuation-time proof and never authorize NAV.


## Follow-up: NAV receipt v3 and callback cohort comparison (2026-10-08)

The observer's heptatrader.account-download.v3 generation preserves v1/v2
historical receipts without relabeling them. New v3 fresh observations require
all three exact account/ledger/positions End callbacks, position callback
timestamps no later than the positions End, complete request-ID scope, an
error-free connection, and the existing bounded receipt span. An incomplete,
overflowed, or cancellation-poisoned session is closed; a capped 10/20/40/80/120
second backoff prevents recurring failed handshakes on every 10-second poll.

Rejected rejected-raw.json remains NOT accepted NAV. The diagnostic's
cohort_comparison checks all required currency and BASE fields, positions,
distinct request-ID cohorts, exact account/client/period, completed end
barriers and independent receipt windows. Every compared field difference is
retained. BASE changing while rates remain unchanged is categorized only as
BROKER_UPDATE_ORDERING_POSSIBLE_NO_COMMON_MARK. This is a hypothesis, NOT an
assertion of true causality or a common source valuation timestamp. Repeated
identical values are likewise NOT a common Broker-time certificate. The USD
1.00 reconciliation threshold remains unchanged; failures are not published
as accepted NAV and cannot become the opening reset baseline.

Native regression covers second successive stale secondary CASH renewal,
full broker connection-epoch transition, late old-epoch callback rejection,
and independent new BID/ASK for BTC and EUR. This proves only deterministic
source behavior, not a live disconnect or order-safety qualification.

Release blocker: IB's account/ledger/positions multi-request initial-download
End callbacks do not carry the underlying source valuation timestamp. Genuine
common Broker-time proof for every currency plus BASE cannot be constructed
from locally received callbacks or an executable FX midquote. An independent
Broker source with explicit common valuation time/basis and matching currency
coverage is required for the requested strict same-time certificate. Until
obtained, a mismatching account currency/BASE NAV remains rejected under the
original USD 1.00 threshold. The compiled native recovery patch must also
be installed and observed through real feed outage/reconnect and order-send
counter checks before any FX recovery acceptance is claimed.


## Subsequent CASH-farm refusal handling (isolated candidate 2026-10-08)

The real IB wrapper requires a current-epoch positive CASH-farm 2104 before
calling the SDK reqMktData; a 2103/lost lease can therefore cause a **definite
pre-SDK, no-send** result during secondary EUR renewal. A single refusal must
not terminate an otherwise healthy mixed BTC-primary PAPER runtime or strand
EUR in an unrenewable missing state.

For an unchanged, connected, ingress-healthy epoch, record the unissued EUR
request ID as refused; leave the EUR quote Missing and allow a **new** request
ID after the existing 15-second cooldown. The replacement cancels only a
previously sent market-data request, never an unissued ID, and never alters
BTC's request identity. An SDK exception, uncertain cancellation, ingress
fault, or changed/disconnected broker epoch remains fatal to this local
renewal pathway; these conditions are not interpreted as safe no-send.

A recovering CASH-farm 2104 only permits a new market-data request: EUR
cannot become fresh until positive, un-crossed new BID/ASK arrive on its own
request ID in that epoch. Delayed/old-ID callbacks remain ignored. No broker
order-send, order-cancel, or trader restart is included in this patch.

Regression uses a Debug native test with assertions enabled to exercise two
successive no-send refusals, cooldown, missing-state preservation, independent
BTC identity, old-ID rejection, fresh dual-leg revival, and epoch refusal.
The native IB SDK executable and loopback probe build/test are separate from
production deployment. Before approving FX recovery, require a controlled real
farm-down/up and reconnect with unchanged order send counters and reconciled
existing broker order/position identity.

**NAV remains independent**: the observer's all-End callback cohort is a
transport-receipt proof, not a Broker source-valuation timestamp. If currency
translation and BASE do not reconcile under the unchanged USD 1.00 threshold,
even after consistent repeated cohorts, NAV must remain rejected. Only a
Broker-issued common timestamp/basis covering every currency and BASE could
support the requested true same-valuation-time certificate; callback arrival
times and quote midpoints cannot fabricate it.

## Read-only independent CASH quote witness (2026-10-08)

The diagnostic entrypoint `scripts/hepta_fx_readonly_probe.py` runs in a
**separate isolated client ID 20000..30000**. It rejects IDs owned by the
current PAPER period or NAV observer, uses the pinned Python SDK and local
IB Gateway PAPER port 4002 only, and permits just `startApi`,
`reqMarketDataType(1)`, the exact `EUR/CASH/IDEALPRO/USD`
`reqMktData` wire and cancellation of its own ticker ID. Every order,
foreign market data, account read, and global cancellation send is forbidden.
No source or output is written to NAV/OMS state. Responses contain no account
ID, credentials or Broker message text.

Example (diagnostic ONLY, not a production acceptance declaration):

```bash
python3 -B scripts/hepta_fx_readonly_probe.py \
  --sdk "$HOME/.local/share/heptatrader/vendor/ibapi-reset-check" \
  --client-id 29555 --cycles 2 --window-sec 8 --reconnect-delay-sec 12
```

The test requires two distinct fresh positive BID/ASK tickPrice callbacks
with the instrument's current request ID and a Broker
`marketDataType(reqId, 1)` witness. A 2104 market-data farm connected
callback is **not** per-instrument quote proof. Delayed/other data modes
cannot authorize quotes. The only output is a diagnostic; it never
certifies a common NAV valuation timestamp.

Real read-only execution on the running PAPER Gateway: both connections
handshook, both sent exactly one streaming EUR.USD subscription and cancelled
it, but across two separate 8-second windows received **zero**
`marketDataType` and **zero** `tickPrice` callbacks.
Both had informational farm 2104/2107/2158 codes and no request-specific
Broker error callback. This is a **FAILED real quote recovery gate**, not
evidence that farm 2104 restored live FX quotes. No broker order mutation was
sent. A live entitlement / paper market-data sharing problem, a per-symbol
Broker routing problem, or terminal-side quote delivery fault remains
unresolved; no diagnosis of which cause is final without further Broker
evidence. Do not restart the trading owner or permit stale quotes to trade
to paper over this.

The real FX outage + reconnect qualification requires evidence from the
*candidate native execution* in addition to this independent read-only
witness: observe quote request IDs across farm loss/restoration, new live
BID/ASK, quote freshness continuity, current order/position ownership and
**unchanged broker/OMS send counters**. The production Execution binary
continues to run the previous release until these gates pass.

The Broker's Web API ledger `timestamp` is documented as data retrieval
time, not one underlying valuation time for all currencies and BASE. A
10-second WebSocket ledger topic can contain blank (unchanged) currencies.
Neither is a justified substitute for a source-proven atomic NAV mark.

## Broker CASH-farm scoped reproduction and live quote recovery

Subsequent independent PAPER API tests narrowed the earlier negative probe:

- Contract details: the exact EUR/CASH/IDEALPRO/USD contract resolved to a Broker conId on an order-incapable diagnostic client. A malformed contract is not supported by this evidence.
- In the original two-cycle experiment the diagnostic sent `reqMktData` before proving a scoped CASH-farm 2104. It received no per-ticker callbacks. These early samples cannot establish absent FX entitlements.
- A separate diagnostic request for type 3 led the Broker to respond with `marketDataType=1` and real BID/ASK callbacks (fields 1 and 2). Delayed mode must never be substituted in the native production quote path; this was diagnostic evidence that live data can be delivered.
- The read-only probe now waits for a 2104 whose trailing farm identifier is exactly `cashfarm` or `hfarm`, maintains a bounded stability interval, rejects 2103/2119 losses, and then requests **mode 1** and the pinned EUR ticker. Informational 2104 for unrelated farms cannot authorize the request.
- Under this scoped-farm sequence an 8-second probe returned `LIVE_BID_ASK`, bid 1.12021, ask 1.12023, oldest leg age 93 ms, market mode 1 and 13 price callbacks. A later 20-second window also returned `LIVE_BID_ASK`, with last bid/ask leg ages 1470 ms and 29 events at which both legs satisfied the existing 5-second limit.
- A separate two-cycle 8-second reconnect observation received the scoped CASH farm and market mode 1 in both cycles, and had 15 / 9 price callbacks, but neither window finished with a simultaneously fresh pair. This is intermittent current freshness, not an entitlement diagnosis or a steady-state recovery proof.

Every probe instance has a distinct 20000..30000 client ID, checked against the live trader and NAV observer; its send boundary admits no order API. Quote callback freshness uses the existing 5-second limit. It never restamps an unchanged BID or ASK and never publishes NAV.

**Release scope remains unchanged:** read-only successes cannot qualify production Execution's real farm-down/up and reconnect state machine, nor prove zero duplicate order sends across those events. Its candidate binary is not installed. The NAV v3 multi-request End receipt likewise does not carry an atomic Broker valuation time, so a USD >1 discrepancy remains rejected.


## Same-window live FX and rejected NAV comparison (2026-10-08, read-only)

A fresh owner-isolated EUR/USD probe on the running PAPER Gateway and the
unchanged deployed NAV v3 observer were sampled concurrently. This is only
a source-provenance comparison and **not** a common Broker valuation mark.

- The reconnect probe completed three independent sessions using a distinct
  order-incapable client. All returned CASH-farm readiness, market mode 1,
  and 29 / 44 / 29 price callbacks, but 5-second two-leg readiness at the
  **end** was false / true / false. The failing windows ended with stale
  ASK ages 8747 and 7999 ms while BID ages were 1603 and 2279 ms. Thus
  valid live callbacks exist, but updating only BID must not re-stamp ASK.
- The concurrently collected 20-second probe returned LIVE_BID_ASK with
  81 live tickPrice callbacks. Four distinct
  v3 account receipts within the window showed EUR ledger ExchangeRate
  1.12026 and the same independently calculated, explicitly
  *non-authoritative* implied rate 1.119060038967355797198729135901910771021.
- At the nearest account receipt, 795 ms
  from the most recent quote-leg callback, the observed live EUR BID/ASK
  were 1.1191899999999999/1.1192. The algebraic implied rate
  lies outside that spread. The unchanged original reconciliation residual
  was -107.262056686 USD against the existing
  USD 1.00 absolute threshold. Inserting an observed midpoint or an implied
  price is **not** a justified replacement for Broker's source valuation.
- This comparison observes local callback arrival, not proof that the BASE,
  currency ledger, and market quote share one underlying Broker valuation
  time. An unchanged account cohort may simply reflect IB's periodic
  account-update scheduling. Do not classify the discrepancy as purely a
  sequencing error, do not promote rejected raw, and do not relax the USD 1.
- Both probes sent no broker orders. Account/FX driver and Execution were
  not restarted; these receipts therefore do not qualify the candidate
  production FX re-subscription path or its post-restart no-duplicate-order gate.

Local private machine receipts:
`/tmp/heptatrader-fx-nav-temporal-comparison-20261008.json`
(SHA256 `f130f8f521eda51635ab838c6545c87f8c977c63563242458ea17a3130944c89`) and
`/tmp/heptatrader-fxnav-live-reconnect-20261008-1404.jsonl`
(SHA256 `06089fe63481a54584f6bf15aaf07b0367dcdff8f288b6e5ffb19131498be9de`). Neither file is an accepted NAV input.

Potential next isolated read-only evidence lane:
IB `reqTickByTickData(..., "BidAsk", 0, False)` reports both legs with
one Broker *quote-tick* timestamp; it must be independently qualified under
its market-data quota and per-instrument 15-second re-request restriction.
Even this quote timestamp would **not** certify the account's common
per-currency/BASE NAV valuation instant. No production TBT substitution
or market-data admission change is authorized by this document.


## Fixed-cardinality secondary CASH bid/ask freshness telemetry (2026-10-08)

New candidate Execution **observation only** now projects a bounded
`secondary_cash_quote_count` and, if exactly one secondary CASH instrument
exists, its instrument identity, the current IB market-data request ID,
`dispatch_accepted`, and independently time-stamped BID / ASK ages.

- This is copied from one immutable quote-health record and never sends an
  IB request or trading command. The primary quote-age telemetry remains
  observable when the unrelated secondary FX is Missing; it never fabricates
  a fresher primary or secondary callback.
- A BID update does **not** reset the ASK age; a missing/invalid BID or ASK
  has `*_age_valid=false` and `*_age_ms=0` **only as an absent field marker**.
  The strict telemetry consumer suppresses absent ages and rejects
  claimed valid ages with missing dispatch provenance.
- If no or multiple secondary CASH contracts exist, no ticker is selected:
  `secondary_cash_quote_metrics_present=false` with the explicit count.
  No arbitrary FX symbol is published as representative of all secondaries.
- The `secondary_cash_quote_unfresh_observed_ms` metric is only a
  **conservative lower bound** on consecutive retained 5-second-cadence
  observations where the FX request was not dispatched, or a BID/ASK leg
  was absent, or one leg was older than 5000 ms. It resets after actual fresh
  callbacks, a service-epoch change, a broker connection-epoch change, or
  missing records. It never claims continuous coverage outside the
  sampled interval.
- Fixed-cardinality metrics contain no account, order, command, connection
  credential or market price. They have no authority over sending orders,
  existing quote admission, the original five-second freshness budget or NAV
  authorization. Historical telemetry with all secondary fields absent
  remains valid. Partially present new fields are rejected rather than
  implicitly interpreted as fresh.
- Native C++ serializer/Python report validation exercises both present
  and absent legs, request IDs, scope mismatch, and multi-sample age
  streaks across connection boundaries. A successful build or test does
  **not** attest actual Broker farm-down/up on the candidate binary.
  Deployment of the new Execution binary remains blocked until a controlled
  maintenance window can preserve live FX/BTC order/position ownership and
  demonstrate zero new command send counters.

The concurrently deployed 10-second NAV observer and original USD 1.00
FX/BASE reconciliation are **unchanged**; rejected records remain rejected.
No TWS market-data quote timestamp can substitute for the independently
attested common Broker account valuation time.


## Existing production Execution reconnect failure (2026-10-08 14:32 CST)

During this separate candidate's **read-only test period**, the still-installed
**old** production Execution (binary SHA256
`a7f08557daff9e07eac0b3c8cad2644bdf5710c274fbfae97f4900e28d7d5172`)
experienced an independent Broker reconnect. The relevant systemd/journal
facts (UTC+8) are:

- 14:31:26: risk snapshot connection epoch 5 -> 6, connected=false,
  `IB_RISK_CONNECTION_CHANGED`, risk/coherence temporarily incomplete.
- 14:31:56: Broker connection epoch 7 connected=true, risk still incomplete.
- 14:32:06: risk/coherence and recovery barrier observations returned complete.
- **14:32:14**: old Execution exited fail-closed with
  `IB_PAPER_BROKER_RECONNECT_SNAPSHOTS_UNREADY`, systemd exit status 9.
  This generic reason does not by itself prove which recovery leg remained
  incomplete; a visible complete risk projection is not equivalent to
  the full terminal/active/order/exposure/quote readiness conjunction.
- **14:32:45**: systemd automatic restart, old installed binary.
  **14:32:52**: reported runtime ready. New service and Broker epoch
  identities are distinct from the previous process; no source code
  candidate was deployed to produce this recovery.
- Read-only sample after restart had connected=true,
  event_stream_authoritative=true, coherent_risk_complete=true,
  zero active orders and fresh BTC primary quote age in recent samples.
  OMS journal accumulated `place_sent=25` and `flatten_sent=31`,
  **with no new send records since 14:20**, including the restart interval.

This is **not** a passed native candidate fault-injection result and not a
guarantee against future duplicate broker commands. A fail-closed crash
followed by automatic restart is a distinct production-reliability concern;
the candidate must preserve its own command/owner/position identities
and verify new EUR BID/ASK request epochs under controlled Broker feed-loss,
farm restoration, connection loss, and later reconnect. Do not dismiss
unresolved terminal/active/exposure barriers merely because the
read-only risk summary becomes complete.

The code change in this candidate adds only observation fields; this
incident is documented separately as non-authorizing forensic evidence.
It does **not** widen NAV's USD 1.00 threshold or authorize
a rejected account valuation.


## Intermittent numeric NAV success is not same-source-time proof (14:43–14:45 UTC+8)

During the same verified, unchanged deployed NAV v3 observer, the Broker's
account values changed without any new same-source valuation certificate.
A 14:43:07 rejected account cohort had **-9.835326686 USD**
non-accrual FX/BASE residual, exceeding the original 1.00 USD threshold.
This rejected raw remains present as a distinct diagnostic and is **not**
an accepted NAV or reset opening.

Later full **new** v3 receipts were source-bound to their own `raw_sha256`,
had all three request Ends, independent callback arrival windows, and no
transport errors. One accepted 14:44+ receipt had **-0.895112486 USD**
non-accrual FX/BASE residual, so it passed the **existing** financial
test without relaxing the 1.00 USD threshold. The original full-currency
NLV difference **-1.665279095 USD** remains explicitly present in
`fx_reconciliation_difference`: this includes accrued cash translated
at current ledger FX even though Broker BASE accrued cash is reported on
its own daily/transaction-rate basis. It is **not** the original
non-accrual $1 financial acceptance comparison and is not discarded.

The accepted receipt and previously rejected receipt have distinct
raw SHA256 digests and distinct request-ID cohorts. This is not a
promotion of rejected raw. A later standalone account observation may
fail again if Broker values move or if the OMS journal changes during
read-only capture; neither `nav_complete=true` nor a run of
arithmetically coherent cohorts provides a common **Broker source
valuation tick**. Every observed v3 cohort still explicitly carries
`broker_valuation_tick_attested=false`.

**Do not claim common NAV valuation-time qualification** from this
intermittent numeric reconciliation. Keep two independent acceptance
dimensions in operator reporting: exact transport + original fixed-bound
financial reconciliation, and separately independently established
Broker valuation timestamp/basis for all material currencies and BASE.
The latter remains missing and is a release blocker.
