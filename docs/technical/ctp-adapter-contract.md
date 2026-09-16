# CTP 6.7.7 adapter implementation contract

Status: PROPOSAL
Applies to: future CTP execution process; current `ctp-adapter` remains EXPERIMENTAL and fail-closed
Vendor boundary: operator-supplied CTP 6.7.7 SDK overlay verified against `third_party/ctp/6.7.7/manifest-v1.json`

## Objective and authority boundary

This contract turns the existing negative-capability CTP stub into an implementable plan without creating a second order authority. The Execution Service remains the only owner of durable command identity, journal-before-send, session fencing, deterministic risk and reconciliation. A future CTP adapter may translate one validated Execution mutation into CTP API calls and normalize callbacks; it may not accept Agent credentials, mint command IDs, make strategy decisions, or infer success from transport availability.

Until every acceptance item in this document is implemented and executed, `Connect`, query, place and cancel remain unsupported and CTP must not be advertised by discovery or deployment.

## Vendor input and build boundary

The repository does not redistribute the CTP SDK. The only supported development input is a separately controlled CTP **6.7.7** overlay whose required headers match the byte sizes and SHA-256 digests in `third_party/ctp/6.7.7/manifest-v1.json`. Platform libraries are external deployment inputs and are intentionally absent from Git.

A future CMake option must require explicit paths for both headers and the platform trader/market-data libraries. Configuration must fail when the overlay is absent, the manifest does not match, the selected library architecture differs from the target, or distribution/licensing review is not recorded. No fallback may rediscover retired `Interface/CTPTradeApi*` paths.

The CTP-linked daemon must be a distinct privileged execution target. Agent, Tool Gateway, simulator and generic Execution client targets must not link the vendor libraries or inherit CTP credentials/network access.

## Connection and session state machine

The adapter state is explicit and monotonic within one connection epoch:

```text
DISCONNECTED
  -> FRONT_CONNECTING
  -> FRONT_CONNECTED
  -> AUTHENTICATING          (when broker requires authenticate)
  -> LOGGING_IN
  -> SETTLEMENT_QUERYING
  -> SETTLEMENT_CONFIRMING
  -> RECONCILING
  -> READY

any disconnect/error/epoch change
  -> FENCED
  -> DISCONNECTED or RECONCILING
```

`READY` requires all of the following for the same connection epoch: successful front connection, successful authentication when configured, successful investor login, current trading day, settlement-info query completion, settlement confirmation, instrument/account/position/order/trade refresh barriers, and reconciliation of every locally uncertain mutation. A callback from an older epoch is ignored for admission and retained only as diagnostic evidence when safely attributable.

Request IDs are process-owned monotonic identifiers and are not mutation identities. CTP `OrderRef` is service-owned and must be durably bound to the Hepta command/correlation before order insertion. Front/session IDs and exchange order system IDs arrive later and augment the correlation; they never replace the durable Hepta command key.

## Canonical correlation model

One local order correlation record contains at least:

- Hepta owner `(agent_id, session_id, execution_domain, account)`;
- stable `command_id` and normalized request hash;
- service-owned venue correlation / CTP `OrderRef`;
- CTP broker ID, investor ID and trading day;
- front ID and session ID when known;
- exchange ID and `OrderSysID` when known;
- instrument ID;
- insert request ID;
- current connection epoch;
- latest normalized order state;
- cumulative traded volume and remaining volume;
- terminal evidence state;
- all trade IDs used for economic deduplication.

An order callback that cannot be matched without ambiguity must not create a new local owner. A trade callback requires an exact account/instrument/order correlation and a trade identity; duplicated callbacks must be economically idempotent.

## Instrument and exchange identity

A CTP instrument is not identified by symbol text alone. The authoritative contract identity binds at minimum instrument ID, exchange ID, product class, volume multiple, price tick and the current trading day/session facts required by the risk policy. Futures/options contract metadata comes from a completed instrument query or reviewed static configuration tied to the same SDK/trading environment.

Prices must satisfy finite/positive checks and exchange price-tick constraints. Volumes must satisfy positive integer lot semantics. The adapter must not round an invalid Agent quantity into a valid exchange quantity after risk approval.

## Open/close and SHFE/INE close semantics

The Execution-side intent must use an explicit economic effect: open, close, close-today or close-yesterday. The adapter never guesses close-today allocation from a single net position.

For exchanges where close-today and close-yesterday are distinct, authoritative position state must retain today/yesterday long and short quantities separately. A reducing order is admitted only if its requested effect can be proved from that position snapshot. Any automatic split, if introduced, is an Execution-owned plan containing individually journaled child mutations; the adapter itself may not silently turn one command into multiple external sends.

For exchanges whose close flag does not distinguish those inventories, the normalized plan still records which authoritative quantities justified the close so replay and reconciliation remain deterministic.

## Order insertion boundary

Before `ReqOrderInsert` the Execution Service must already have durably committed:

1. normalized mutation identity and request hash;
2. owner/account/domain/instrument binding;
3. authoritative risk snapshot identity;
4. service-owned venue correlation / `OrderRef`;
5. a send-attempt record.

The adapter returns a typed outcome with the same semantics as other immediate venues:

- **RejectedBeforeSend** only when validation or a synchronous API result proves the vendor call was not invoked;
- **Submitted** when the API call was invoked and accepted for asynchronous processing; this is not exchange acceptance;
- **Uncertain** for exceptions, ambiguous return state, disconnect during send, callback loss or any failure after a possible invocation.

An asynchronous `OnRspOrderInsert`, `OnErrRtnOrderInsert` or order callback can later reject an already submitted attempt. Such rejection is a venue result and must be journaled against the original command; it does not authorize generation of a fresh command ID.

## Cancel boundary

Cancellation requires a locally owned active order plus enough CTP identity to address the current venue order safely. The request binds `OrderRef`, front/session where required, and exchange/order-system identity when available.

A synchronous inability to construct a valid action request is a pre-send refusal. Once `ReqOrderAction` may have been invoked, exceptions, ambiguous return values or disconnect are uncertain until authoritative order/trade queries resolve the state. A terminal cancelled order without additional trade proves cancellation only for the remaining quantity; fills already observed remain economic truth.

## Authoritative refresh barriers

Each query family has a generation owned by the CTP execution process. Completion is the matching final-response callback for the same request/epoch, not receipt of one row. At minimum the READY/recovery barrier contains:

| Domain | Minimum authoritative facts | Completion rule |
|---|---|---|
| instruments | contract metadata needed by enabled products | final instrument response for generation |
| account | balance/margin/available funds used by policy | final trading-account response |
| positions | direction, today/yesterday quantities, cost/margin fields used by policy | final investor-position response |
| active orders | all orders visible to this investor/session scope plus stable correlations | final order-query response |
| trades | all executions required to explain fills/recovery | final trade-query response |
| settlement | current trading-day statement and confirmation state | query + confirmation success |

Timeout, error callback, request-ID mismatch, disconnect or callback conflict marks the affected generation incomplete. Incomplete snapshots cannot authorize risk increase.

## Recovery and reconciliation

On restart/reconnect:

1. replay OMS before opening mutation admission;
2. establish a new CTP connection epoch;
3. login and complete settlement handling;
4. obtain complete order, trade, position and account barriers;
5. match uncertain commands by durable `OrderRef` and later CTP/exchange identities;
6. reconstruct owner/order state and cumulative fills;
7. reject unmapped locally owned active orders, duplicate correlations, unexplained trades or contradictory position evidence;
8. release the reconnect fence only after all possible sends are resolved or explicitly remain blocked.

Absence from an incomplete query is never rejection evidence. An order absent from the active-order set is not terminal until order/trade evidence explains its outcome. Reconciliation must survive another restart without re-sending the original command.

## Risk inputs

A CTP qualification profile must declare the exact exchanges/products/instruments and the authoritative conversion inputs used by generic risk. At minimum futures risk binds volume multiple, price tick, reference/limit price, account currency, position direction and pending active-order exposure. Margin ratios supplied by CTP are observations, not permission to omit configured hard notional/quantity limits.

Before any multi-product qualification, the Execution authoritative snapshot must express base-currency notional/margin consistently across enabled contracts. Heterogeneous contract quantities may not be summed as a portfolio measure.

## Failure matrix

| Failure | Required result |
|---|---|
| vendor overlay missing/digest mismatch | build/configure failure |
| front disconnect | new epoch + mutation fence |
| authenticate/login failure | not READY; no mutation |
| settlement unconfirmed | not READY; no mutation |
| request ID exhaustion/collision | fail closed; restart/incident |
| `ReqOrderInsert` precondition failure before call | rejected-before-send |
| possible insert call + throw/ambiguous result | uncertain |
| insert asynchronous rejection | durable rejected venue outcome |
| duplicate/out-of-order order callback | monotonic projection or explicit conflict/incomplete state |
| duplicate trade callback | one economic application by exact trade identity |
| cancel possible-send ambiguity | uncertain; reconcile |
| trading-day change | fence, refresh all day-bound state, re-establish barriers |
| position/order/trade query timeout | snapshot incomplete; block risk increase |
| unknown active order owned by account | recovery block / operator incident |

## Observability contract

Fixed-cardinality outputs must cover connection state/epoch, login and settlement state, refresh generations/completeness/duration, request failures, callback lag/conflicts, order/trade correlation counts, uncertain commands, reconciliation duration/result, active orders, position snapshot age and risk rejection reason. Account numbers, credentials, auth codes and free-form broker messages are never metric labels.

Every metric family needs explicit presence/freshness semantics; an absent producer must not be exported as numeric zero.

## Required executable acceptance before status promotion

A future implementation may change `ctp-adapter` from EXPERIMENTAL only after all of these are behavior-tested against the actual adapter composition:

- exact SDK-overlay and wrong-architecture rejection;
- connection/auth/login/settlement state transitions and reconnect epochs;
- instrument/account/position/order/trade generation barriers;
- open, close, close-today and close-yesterday mapping at exact boundaries;
- service-owned `OrderRef`, front/session and `OrderSysID` correlation;
- accepted, asynchronously rejected and uncertain insert outcomes;
- partial fills and duplicate/out-of-order order/trade callbacks;
- cancel before/after acknowledgement and cancel/fill race;
- disconnect at every durable/send boundary followed by restart and no resend;
- trading-day rollover;
- stale/incomplete snapshot rejection;
- authoritative reconciliation of active, terminal and executed orders;
- final guarded flatten/close back to authoritative flatness;
- process/credential/network isolation and exact release-artifact identity.

Synthetic vendor fixtures are required for deterministic fault injection, but they do not constitute venue qualification. Real CTP credentials/front connectivity and broker-observed acceptance remain a separately authorized external campaign. LIVE remains unavailable until an explicit qualification policy says otherwise.
