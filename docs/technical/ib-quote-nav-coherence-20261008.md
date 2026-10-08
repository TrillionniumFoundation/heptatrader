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


## Sampled, source-bound FX quote continuity (2026-10-08)

The read-only EUR/CASH/IDEALPRO/USD probe now measures a bounded 100-ms
sampling window independently of the callback counters. Each sample requires
**both** last-arriving current-request BID and ASK to be individually within
the original 5-second freshness policy, market-data type 1, an uncrossed
positive pair, and the scoped CASH-farm lease still present. Samples never
re-stamp a leg. The resulting `freshness_window` receipt includes the exact
sample numerator/denominator, first and last fresh sample times, time lacking
a fresh pair **after** its first appearance, its longest consecutive missing
streak, and farm-down samples. It never claims a Broker NAV valuation mark,
production recovery, or future quote availability.

Real IB PAPER isolated client ID 29567, THREE distinct 20-second streaming
subscriptions, disconnected between cycles (12-second gaps), on 2026-10-08:

| Cycle | Result | Quote callbacks | Fresh pair samples | Integer-floored coverage | Longest post-first missing |
|--|--|--:|--:|--:|--:|
| 1 | LIVE_BID_ASK | 50 | 165/201 | 82% | 3,104 ms |
| 2 | LIVE_BID_ASK | 36 | 196/201 | 97% | 0 ms |
| 3 | LIVE_BID_ASK | 46 | 182/201 | 90% | 901 ms |

The exact Broker trace is retained OUTSIDE NAV and OMS state as immutable
operator evidence under
`~/.local/state/heptatrader/operator-evidence/fxnav-freshness-window-20261008/`,
SHA256
`dfde97edff1362e8ea6ff1b96a86a9989c24b81c41cd3ee65370390b85dac47a`.
All cycles received scoped CASH farm readiness, Broker marketDataType=1 and
positive live legs. The wire allowlist counted one each of startApi (71),
market-data-type (59), exact reqMktData (1) and owned cancelMktData (2)
PER cycle, with zero trading send opcodes. The independent OMS journal
place_sent=25/flatten_sent=31 remained unchanged across this test.

**What this supports:** separate, order-incapable API connections can observe
real quotes after reconnection. Partial gaps and intermittent missing/stale
samples are real and are not discarded. The longest *sampled consecutive
missing duration* in these three windows was below 5 seconds; this is NOT
equivalent to 'every quote remained under 5 seconds old' or uninterrupted
market-data service.

**What it does not support:** a farm-down/up fault injected into the native
Execution owner, automatic native renewal/new request-ID validation under
that fault, continuous future quote coverage, zero native replay sends after
a real owner reconnect, or a same-valuation-time cross-currency NAV proof.
All original fail-closed admission, expiry, and USD 1 NAV gates remain.

## Initial optional CASH quote no-send: independent source fix (2026-10-08)

The first mixed BTC-primary/EUR-secondary quote plan can legally accept a
locally refused optional EUR `ReqMktData` while continuing the fresh BTC
ticker. The previous renewal guard required `dispatchAccepted` or an
existing *renewal* flag; therefore a first-cycle definite no-send never
became retryable. In a Debug regression this path failed at
`retry.accepted && retry.cancelRequestIds.empty()` before the fix.

The revised quote state records `dispatchResultRecorded` separately from
`dispatchAccepted`. Pending or unknown dispatch outcomes **cannot** be
replaced, even after 15 seconds. Both initial and renewed EUR subscriptions
with an explicit `RecordDispatchResult(false)` may be retried after the
unchanged 15-second cooldown with a distinct request ID and **without**
cancelling the never-sent ID. An SDK exception, missed dispatch receipt,
unhealthy ingress, farm warning or connection-epoch change retains its
existing fail-closed behavior. A duplicate dispatch receipt is rejected.

The new deterministic test drives initial unissued EUR, a pending renewal,
a second pre-SDK refusal, and farm recovery; it rejects old-ID ticks, demands
two real new BID/ASK legs to restore EUR, preserves the primary BTC ID and
rejects cross-epoch renewals. The identical test was red before this change
and green after it, with C++ assertions enabled. No OMS or order sends are
part of this code path.

Independent read-only PAPER proof on client ID 29571 subsequently obtained
two complete live EUR/USD BID/ASK pairs in separate 20-second windows,
with `marketDataType=1`, stable scoped `cashfarm` receipt and exactly one
pinned subscription/cancel per cycle, no order opcodes. This shows the
Broker CAN supply fresh EUR data to an isolated client; it does **not**
qualify native Execution's own reconnect nor certify a Broker NAV mark.

The original fixed USD 1.00 NAV threshold is unchanged; rejected raw remains
ineligible for account NAV, closing PnL or a new opening.
