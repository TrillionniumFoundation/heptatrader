# XT/QMT execution-adapter implementation contract

Status: PROPOSAL with CURRENT read-only HXQ1 subset
Applies to: selected next venue after the canonical simulator and qualified IB PAPER path
Owner boundary: Execution Service remains the sole order authority

## Implementation status

The repository currently implements only the native **HXQ1 v1 read-only client subset** in `HeptaTrade/adapter_xt/xt_gateway_adapter.{h,cpp}`:

- four-byte unsigned big-endian payload length followed by one bounded UTF-8 JSON object;
- 256 KiB maximum JSON payload;
- Execution-owned request IDs plus service epoch, connection epoch and exact account binding;
- trusted expected account currency plus a non-empty bounded normalized instrument universe supplied by the qualification profile; position/order/trade payload rows and quote requests outside that universe fail closed before they can become read authority;
- exact identity handshake acknowledgement;
- strict typed `account_snapshot`, `position_snapshot`, `order_snapshot`, `trade_snapshot`, and `quote_subscribe` response payloads through an injected already-admitted read-only exchange;
- completed known-empty position/order/trade snapshots, bounded unique identities and explicit account/order/trade/quote numeric validation;
- account+position and account+position+order+trade readiness barriers requiring the configured connection epoch and shared non-zero generation; the latter also binds trade rows to matching order identity/instrument/side and requires positive-fill orders to have trade evidence;
- quote freshness evaluated against an Execution-owned evaluation clock and explicit maximum age;
- exact response binding plus fail-closed malformed/mismatched/mixed-generation/economically-invalid response behavior, including non-JSON numeric spellings;
- disconnect invalidation of every cached read snapshot;
- hard-disabled place/cancel mutation.

The injected exchange is a protocol seam for qualification; it is **not** a production network implementation. The Windows sidecar, mTLS transport, peer certificates, firewall policy, QMT/`xtquant` runtime, real QMT-produced authoritative payloads, callbacks, mutation messages and qualification remain future work below. For the implemented read-only subset `venue_command_id` is the empty string because no durable venue mutation exists. A non-empty stable `venue_command_id` becomes mandatory only for future mutation/status correlation.

## Why XT/QMT is the selected next adapter

The repository already carries an explicit XT/QMT fail-closed boundary and historical `xtquant` mapping research. CTP requires a separate native vendor SDK, front/login/settlement state and exchange-specific close-today semantics before even a read-only authority can be assembled. XT/QMT is therefore the narrower next integration target for this repository: one Windows/QMT sidecar can isolate the Python/vendor runtime while the Linux/native Agent, Gateway, command identity, OMS, risk and reconciliation contracts remain unchanged.

This selection is a development order, not a claim that a production XT transport exists or is licensed, installed, qualified or authorized. Until the pinned SDK/runtime inputs below are supplied and every acceptance stage passes, `xt-adapter` remains `EXPERIMENTAL`, `transport_implemented=false`, `advertisable=false` and every mutation fails closed.

## Process and trust boundary

The vendor package MUST NOT be imported or linked by Agent, Tool Gateway or the shared Linux Execution coordinator. The selected first topology is fixed rather than left ambiguous:

```text
Agent -> Tool Gateway -> Linux Execution Service
                           |
                           | dedicated HXQ1 v1 over mutually authenticated TLS
                           | exact Windows host/service identity + pinned certs
                           v
                    Windows XT/QMT sidecar
                           |
                           | loopback/local pinned xtquant/QMT API
                           v
                        QMT client
```

Execution remains the sole durable order authority. The future cross-host HXQ1 channel is a new, narrow trust domain owned only by the Execution identity; it is not exposed to Agent/Gateway processes and is not a generic TCP bridge. Both ends pin protocol, peer certificate/public-key identity, allowed host, account/profile digest and message bounds. The Windows firewall accepts the HXQ1 listener only from the exact Execution host; the sidecar's QMT/API access remains local to Windows. A connection without mutual identity/profile agreement is `DISABLED`, not degraded authority.

The sidecar runs under a dedicated Windows service identity. It receives no Agent session token and cannot mint command IDs, decision leases or Execution owner generations. Once mutation exists, it may receive only an Execution-issued venue command whose durable intent and send attempt already exist. Its QMT credential/configuration directory is unreadable by Agent/Gateway identities and by the Linux host. The sidecar is not a generic Python plugin host.

This topology deliberately avoids porting the existing Linux/systemd/Unix-socket Execution authority to Windows merely to obtain process-local IPC. Any future colocated-Windows Execution design or additional network hop is a separate trust-domain change and is outside HXQ1 v1. Until the pinned QMT runtime, certificates, firewall policy and qualification fixture exist, the adapter remains fail closed.

## Pinned external inputs

Before the production mTLS/QMT transport is enabled, one qualification profile must bind all of:

- QMT client product/version and installer digest;
- Python interpreter implementation/version and executable digest;
- `xtquant` package/version and complete installed-package digest;
- account type and a PAPER/simulation-only account identifier for qualification;
- QMT data directory and session namespace;
- sidecar executable/source digest;
- allowed exchange/security/order-price-type matrix;
- exact instrument identity mapping and lot/tick rules;
- HXQ1 mTLS endpoint, certificate/public-key identities, host/service identities and firewall policy;
- qualification fixture/harness digest.

A developer-local import is not a supported SDK. Missing or changed inputs keep the adapter outside qualified transport capability.

## HXQ1 transport protocol

The sidecar protocol is versioned independently of Agent `HTT1`, Execution `HEX1` and supervisor `HSS1`. `HXQ1` version 1 is reserved for the pinned Linux-Execution↔Windows-sidecar mTLS connection above. TLS peer identity is part of transport admission, not a substitute for message-level service/connection epoch checks.

The native read-only codec uses one exact frame:

```text
uint32_be json_bytes
json_bytes bytes of UTF-8 JSON object
```

`json_bytes` must be in `1..262144`, the frame must contain exactly that number of payload bytes, and the payload must be one JSON object. Current native requests are serialized canonically by the client. Identity acknowledgement is accepted only when the complete canonical response string exactly matches the expected protocol/version/request/service epoch/connection epoch/operation/account/`ok=true` object. Read responses require that same exact outer binding followed by one operation-specific canonical payload object. The native account/position/quote parser accepts only the declared field order and primitive grammar, rejects escapes in authority-bearing tokens, non-finite/underflowed numbers, duplicate instruments, extra fields and economic inconsistencies, and bounds positions to 1,024 rows. The future Windows sidecar parser must independently reject duplicate keys, non-finite values and unknown fields before vendor entry; the strict client is defense-in-depth, not permission to emit ambiguous JSON.

Every request carries:

| Field | Requirement |
|---|---|
| `protocol` | exact `HXQ1` |
| `version` | integer `1` |
| `request_id` | transport request ID; never the durable mutation identity |
| `service_epoch` | current Execution service epoch |
| `connection_epoch` | expected XT sidecar/QMT connection epoch |
| `operation` | one of the enabled operations for the current stage |
| `account` | exact qualification-bound account |
| `venue_command_id` | empty for current read-only requests; future mutations/status use the stable Execution-generated correlation |
| `payload` | operation-specific bounded object |

For future mutation/status requests, `venue_command_id` must remain stable across a response-loss retry. Reusing it with a different normalized payload is a protocol conflict. The sidecar must persist or reconstruct enough correlation to answer status after restart before any mutation retry can be considered. A new transport `request_id` never authorizes a new order.

### Operations

**Implemented native read-only subset:**

- `identity` — peer/profile handshake using the configured profile digest;
- `account_snapshot` — typed payload schema `heptatrader.xt.account.v1`: positive generation, completed marker, bounded currency token exactly matching the trusted profile currency, finite non-negative cash/total-asset/available-cash; available cash may not exceed total asset in the first cash-only scope;
- `position_snapshot` — typed payload schema `heptatrader.xt.positions.v1`: positive generation, completed marker and 0..1,024 unique instrument rows with finite non-negative quantity/sellable quantity/cost; sellable quantity may not exceed quantity;
- `order_snapshot` — typed payload schema `heptatrader.xt.orders.v1`: positive generation, completed marker and 0..1,024 unique normalized order rows with exact instrument/side/status, positive quantity/LMT price and status-consistent cumulative fill quantity;
- `trade_snapshot` — typed payload schema `heptatrader.xt.trades.v1`: positive generation, completed marker and 0..1,024 unique trade rows bound to normalized order identity, instrument/side, positive quantity/price and occurrence time;
- `quote_subscribe` — typed payload schema `heptatrader.xt.quote.v1`: positive generation, exact requested instrument, positive finite bid/ask with ask >= bid and positive observation timestamp. Freshness uses the caller's Execution-owned evaluation time, never a payload-supplied current clock.

Completed empty arrays are explicit known-empty evidence. Non-empty position/order/trade rows must use instruments from the trusted finite profile universe, and `quote_subscribe` rejects an unlisted instrument before entering the admitted exchange. `AccountPositionReadReady()` retains the narrower account/position barrier; `AccountPositionOrderTradeReadReady()` requires all four complete snapshots at the configured connection epoch and one non-zero generation, checks trade-to-order identity/instrument/side linkage, rejects trade evidence against a zero-fill order, and requires the bounded per-order trade-quantity sum to match the order's cumulative `filled_quantity` within machine-level relative tolerance. Quote generation remains independently observable. This source subset deliberately does **not** claim the full future venue `READY` state because real sidecar instance identity/health and transport admission are absent.

**Planned read-only operations:** `health`, `quote_unsubscribe`, `command_status`.

**Planned mutation operations:** `place`, `cancel`. Authoritative flatten remains an Execution plan expressed as an exact reduce-only `place`; there is no sidecar-owned flatten policy and no generic raw vendor-call operation. Current `PlaceOrder`/`CancelOrder` never dispatch HXQ1 and fail with `XT_MUTATION_DISABLED` when the read-only seam is configured.

## Connection state machine

The complete future sidecar state machine is:

```text
DISABLED
  -> STARTING
  -> QMT_CONNECTED
  -> ACCOUNT_SUBSCRIBED
  -> REFRESHING
  -> READY

any transport/account/session ambiguity
  -> DEGRADED / RECOVERY_REQUIRED
  -> REFRESHING only after a strictly newer connection epoch

shutdown
  -> DRAINING
  -> CLOSED
```

The current native read-only client has only initialized/disconnected versus identity-handshake-connected state; it does not claim the full `READY` state above. Future `READY` requires the exact account subscription plus complete asset, position, order and trade barriers for the same connection epoch. Quote completeness is tracked per instrument/subscription generation. A socket connection, read-only HXQ1 acknowledgement or successful Python import alone is never full venue readiness.

Every future reconnect increments `connection_epoch`, invalidates quote and account/order snapshot completeness, and fences risk increase until all required barriers are re-established. Sidecar restart also changes an instance identity so stale replies cannot be accepted into a new session.

## Instrument and order identity

The initial instrument key is the full QMT security identity, not a display symbol: `market + stock_code + security_type`. The supported first qualification universe must be finite and source-controlled. The native client now receives that normalized universe as trusted configuration and refuses read authority outside it; the future sidecar must prove that each normalized token maps to the pinned QMT market/security identity, exchange, currency, lot size, price tick and security type.

Future mutation correlation stores all available vendor identities:

- Execution `venue_command_id`;
- QMT account;
- connection epoch;
- sidecar instance ID;
- client/order request sequence when available;
- QMT order ID;
- exchange/order system ID when exposed;
- trade IDs for economic executions.

A numeric QMT order ID without matching account/epoch/instrument provenance is not sufficient to resolve an uncertain send.

## Authoritative refresh barriers

Each future refresh has a sidecar-generated monotonically increasing generation bound to one connection epoch. A snapshot is complete only after the API's corresponding query result has terminated successfully and every row passes identity/numeric validation.

Required first-scope snapshots:

- asset/account: cash, total asset and available cash with explicit currency;
- positions: full security identity, quantity, sellable quantity and cost fields;
- orders: all active and terminal orders needed for command recovery;
- trades: trade ID, order correlation, side, quantity, price and timestamp;
- quotes: bid/ask, observation time, subscription identity and freshness bound.

The implemented account/position/order/trade subset already enforces this rule at the native boundary: each set is known-empty only when the typed response carries `complete=true`, a positive generation and an explicit empty array; absent, malformed or mixed-generation responses never produce the corresponding readiness barrier. The stricter four-snapshot barrier also refuses unmatched trade/order correlation and missing trade evidence for a positively filled order. Full venue readiness still requires real sidecar instance/health and transport admission. Partial callback batches, query exceptions, account mismatch, unsupported security fields or duplicate conflicting keys therefore remain incomplete rather than being inferred from absence.

## Placement contract

This section is a future mutation contract, not current capability. Execution performs owner/session/lease validation, stable command hashing, final risk and durable `intent + send_attempt` before invoking the sidecar.

The sidecar returns one typed disposition:

| Disposition | Required evidence | Execution treatment |
|---|---|---|
| `Submitted` | vendor call returned an accepted request and a valid correlation is retained | await authoritative order/trade callback; not an economic fill |
| `RejectedBeforeSend` | validation failed before vendor mutation entry | durable rejection is permitted |
| `Uncertain` | vendor call entered and response/exception/correlation is incomplete | keep original command ID, fence risk and reconcile |

A Python exception after entering the vendor placement API is `Uncertain`. Boolean false after vendor entry is not automatically a no-send rejection unless the pinned SDK contract provides an independently verifiable pre-send guarantee for that exact code path. Allocation/logging/serialization failure after vendor acceptance is also `Uncertain` and must preserve any known QMT order identity.

## Cancellation contract

This section is also future mutation capability. Cancellation follows the same durable command identity rules. The sidecar may return `Submitted`, `RejectedBeforeSend` or `Uncertain`; a terminal `Cancelled` callback or complete authoritative snapshot resolves the economic/order state. Order absence alone is not successful cancellation. If trade evidence shows the order filled, the cancel resolves as target-terminal/filled, not successful cancel.

## Event normalization

Vendor callbacks are future sidecar work. They are normalized into immutable events before leaving the sidecar. Each event binds sidecar instance, connection epoch, account, full instrument, QMT order ID/correlation, callback type and sidecar receive time. Trade events also bind trade ID and positive finite quantity/price. Duplicate events are idempotent; conflicting reuse of a trade ID or order correlation invalidates the affected snapshot and enters recovery-required state.

Out-of-order status is accepted only through a monotonic state projection. A later callback cannot regress a terminal order to active. `Filled` text without trade or equivalent economic evidence does not by itself prove position change.

## Risk and final-send revalidation

The sidecar is not a portfolio risk authority. Execution owns the policy and final decision. Once mutation is implemented, immediately before vendor mutation the XT binding must revalidate the expected connection epoch, account, instrument mapping, market state, lot/tick rules and quote identity/freshness under the transport send lock. If these inputs changed after preview, reject before vendor entry. Agent-provided reference prices, account names or market identifiers never replace service-owned values.

The first qualification scope should be deliberately narrow: one account, a finite cash-equity universe, limit orders only, one currency and no credit/margin/options. Adding ETFs, convertibles, futures, margin/credit or multiple accounts requires an explicit risk/unit contract rather than reusing quantity as comparable exposure.

## Failure matrix

| Failure | Required behavior |
|---|---|
| sidecar unavailable before read/mutation | read unavailable or future mutation pre-send rejection; no external mutation effect |
| malformed/oversized HXQ1 frame | reject response/request; no mutation |
| stale service or connection epoch | reject before future vendor call |
| read-only acknowledgement identity mismatch | fail closed and do not mark native client connected |
| request timeout after future vendor entry | uncertain; same command only |
| Python/vendor exception after future vendor entry | uncertain |
| callback queue overflow | invalidate affected snapshots, fence risk |
| account mismatch | discard event, record conflict, fence if it touches owned order |
| duplicate exact trade callback | deduplicate |
| conflicting trade ID | recovery required |
| reconnect | increment epoch, clear completeness, full refresh |
| sidecar restart with unresolved send | command status + complete order/trade refresh before any risk increase |
| unsupported security/order type | explicit pre-send rejection |
| shutdown with active owner | terminal/recovery workflow; never silently abandon ownership |

## Observability

Future production runtime output must include sidecar instance presence, connection epoch, connection state, account/position/order/trade refresh generation and completeness, quote generation/age, callback queue depth/overflow count, callback receive-to-normalization latency, HXQ1 request latency/result bins, reconnect count/duration, unresolved command count and last bounded reason code. Account IDs, order IDs, instrument symbols and command IDs are not metric labels.

The current native read-only adapter exposes bounded status/reject reason through its local API and executable capability tests; it is not yet a deployed telemetry producer.

## Development and qualification sequence

1. **Done in source:** implement and test exact native HXQ1 v1 frame codec, request/account/epoch binding, trusted account-currency and finite instrument-universe admission, identity handshake, typed account/position/order/trade/quote payloads, explicit known-empty arrays, same-generation read barriers, trade/order correlation, Execution-clock quote freshness, disconnect invalidation, strict JSON-number/response rejection and mutation disablement.
2. Acquire and hash the actual QMT/xtquant runtime; freeze the qualification profile and supported instrument/order subset.
3. Implement the peer-pinned mTLS transport, Windows sidecar instance identity/health and endpoint/firewall enforcement using the exact HXQ1 v1 frame contract.
4. Drive the implemented read schemas from the pinned runtime and require all required barriers before full venue `READY`; prove reconnect invalidation across a strictly newer connection/instance epoch.
5. Add deterministic callback/overflow fixtures around the real sidecar producer and bind quote subscription lifecycle to that connection/instance epoch.
6. Bind typed placement with durable Execution correlation and pre/post-vendor fault injection; same-ID retry must never duplicate a possible send.
7. Bind typed cancellation and terminal/trade reconciliation.
8. Add restart fixtures that retain unresolved correlations and require complete authoritative refresh before risk opens.
9. Package the exact sidecar/runtime identity, run a simulation/PAPER campaign, finish flat with no unresolved orders, and issue a digest-bound receipt.
10. Only then change capability advertising from experimental/false. LIVE remains a separate qualification decision.

## Acceptance evidence

Promotion from `EXPERIMENTAL` requires executed tests for framing, peer identity, SDK-version mismatch, login/subscription state, every supported instrument/order mapping, partial and duplicate fills, cancel races, queue overflow, disconnect at each durable send boundary, sidecar/Execution restart, old command duplicate and conflict, snapshot generation mixing, stale quote, account mismatch, terminal reconciliation and final flatness. The real pinned QMT runtime must participate in at least the qualification lane; synthetic callback fixtures or the injected read-only exchange alone cannot promote transport or authorization status.
