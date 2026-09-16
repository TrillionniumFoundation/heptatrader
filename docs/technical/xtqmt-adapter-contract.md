# XT/QMT execution-adapter implementation contract

Status: PROPOSAL
Applies to: selected next venue after the canonical simulator and qualified IB PAPER path
Owner boundary: Execution Service remains the sole order authority

## Why XT/QMT is the selected next adapter

The repository already carries an explicit XT/QMT negative-capability boundary and
historical `xtquant` mapping research. CTP requires a separate native vendor SDK,
front/login/settlement state and exchange-specific close-today semantics before even
a read-only authority can be assembled. XT/QMT is therefore the narrower next
integration target for this repository: one Windows/QMT sidecar can isolate the
Python/vendor runtime while the Linux/native Agent, Gateway, command identity,
OMS, risk and reconciliation contracts remain unchanged.

This selection is a development order, not a claim that XT transport exists or is
licensed, installed, qualified or authorized. Until the pinned SDK/runtime inputs
below are supplied and every acceptance stage passes, `xt-adapter` remains
`EXPERIMENTAL`, `transport_implemented=false`, `advertisable=false` and every
mutation fails closed.

## Process and trust boundary

The vendor package MUST NOT be imported or linked by Agent, Tool Gateway or the
shared Linux Execution coordinator. The selected first topology is fixed rather
than left ambiguous:

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

Execution remains the sole durable order authority. The cross-host HXQ1 channel is
a new, narrow trust domain owned only by the Execution identity; it is not exposed
to Agent/Gateway processes and is not a generic TCP bridge. Both ends pin protocol,
peer certificate/public-key identity, allowed host, account/profile digest and
message bounds. The Windows firewall accepts the HXQ1 listener only from the exact
Execution host; the sidecar's QMT/API access remains local to Windows. A connection
without mutual identity/profile agreement is `DISABLED`, not degraded authority.

The sidecar runs under a dedicated Windows service identity. It receives no Agent
session token and cannot mint command IDs, decision leases or Execution owner
generations. It may receive only an Execution-issued venue command whose durable
intent and send attempt already exist. Its QMT credential/configuration directory
is unreadable by Agent/Gateway identities and by the Linux host. The sidecar is not
a generic Python plugin host.

This topology deliberately avoids porting the existing Linux/systemd/Unix-socket
Execution authority to Windows merely to obtain process-local IPC. Any future
colocated-Windows Execution design or additional network hop is a separate trust-
domain change and is outside HXQ1 v1. Until the pinned QMT runtime, certificates,
firewall policy and qualification fixture exist, the adapter remains fail-closed.

## Pinned external inputs

Before transport code is enabled, one qualification profile must bind all of:

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

A developer-local import is not a supported SDK. Missing or changed inputs keep the
adapter in negative-capability mode.

## HXQ1 transport protocol

The sidecar protocol is length-prefixed and versioned independently of Agent
`HTT1`, Execution `HEX1` and supervisor `HSS1`. `HXQ1` version 1 runs only on the
pinned Linux-Execution↔Windows-sidecar mTLS connection above. TLS peer identity is
part of transport admission, not a substitute for message-level service/connection
epoch checks. Frames are bounded to 256 KiB and contain one canonical UTF-8 JSON
object with duplicate keys and non-finite numbers rejected. Unknown fields are
rejected for mutation messages.

Every request carries:

| Field | Requirement |
|---|---|
| `protocol` | exact `HXQ1` |
| `version` | integer `1` |
| `request_id` | sidecar-transport request ID; never the durable mutation identity |
| `service_epoch` | current Execution service epoch |
| `connection_epoch` | expected XT sidecar/QMT connection epoch |
| `operation` | one of the operations below |
| `account` | exact qualification-bound account |
| `venue_command_id` | Execution-generated stable correlation derived from durable owner/session/command identity |
| `payload` | operation-specific bounded object |

`venue_command_id` must remain stable across a response-loss retry. Reusing it with
a different normalized payload is a protocol conflict. The sidecar must persist or
reconstruct enough correlation to answer status after restart before any mutation
retry can be considered. A new transport `request_id` never authorizes a new order.

### Operations

Read operations: `identity`, `health`, `account_snapshot`, `position_snapshot`,
`order_snapshot`, `trade_snapshot`, `quote_subscribe`, `quote_unsubscribe`,
`command_status`.

Mutation operations: `place`, `cancel`. Authoritative flatten remains an Execution
plan expressed as an exact reduce-only `place`; there is no sidecar-owned flatten
policy and no generic raw vendor-call operation.

## Connection state machine

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

`READY` requires the exact account subscription plus complete asset, position,
order and trade barriers for the same connection epoch. Quote completeness is
tracked per instrument/subscription generation. A socket connection or successful
Python import alone is never `READY`.

Every reconnect increments `connection_epoch`, invalidates quote and account/order
snapshot completeness, and fences risk increase until all required barriers are
re-established. Sidecar restart also changes an instance identity so stale replies
cannot be accepted into a new session.

## Instrument and order identity

The initial instrument key is the full QMT security identity, not a display symbol:
`market + stock_code + security_type`. The supported first qualification universe
must be finite and source-controlled. Mapping to any canonical HeptaTrader
instrument includes exchange, currency, lot size, price tick and security type.

Order correlation stores all available vendor identities:

- Execution `venue_command_id`;
- QMT account;
- connection epoch;
- sidecar instance ID;
- client/order request sequence when available;
- QMT order ID;
- exchange/order system ID when exposed;
- trade IDs for economic executions.

A numeric QMT order ID without matching account/epoch/instrument provenance is not
sufficient to resolve an uncertain send.

## Authoritative refresh barriers

Each refresh has a sidecar-generated monotonically increasing generation bound to
one connection epoch. A snapshot is complete only after the API's corresponding
query result has terminated successfully and every row passes identity/numeric
validation.

Required first-scope snapshots:

- asset/account: cash, total asset and available cash with explicit currency;
- positions: full security identity, quantity, sellable quantity and cost fields;
- orders: all active and terminal orders needed for command recovery;
- trades: trade ID, order correlation, side, quantity, price and timestamp;
- quotes: bid/ask, observation time, subscription identity and freshness bound.

Partial callback batches, query exceptions, account mismatch, unsupported security
fields or duplicate conflicting keys make the generation incomplete. Known-empty
is represented by a completed empty snapshot, not by timeout or missing callbacks.

## Placement contract

Execution performs owner/session/lease validation, stable command hashing, final
risk and durable `intent + send_attempt` before invoking the sidecar.

The sidecar returns one typed disposition:

| Disposition | Required evidence | Execution treatment |
|---|---|---|
| `Submitted` | vendor call returned an accepted request and a valid correlation is retained | await authoritative order/trade callback; not an economic fill |
| `RejectedBeforeSend` | validation failed before vendor mutation entry | durable rejection is permitted |
| `Uncertain` | vendor call entered and response/exception/correlation is incomplete | keep original command ID, fence risk and reconcile |

A Python exception after entering the vendor placement API is `Uncertain`. Boolean
false after vendor entry is not automatically a no-send rejection unless the pinned
SDK contract provides an independently verifiable pre-send guarantee for that exact
code path. Allocation/logging/serialization failure after vendor acceptance is also
`Uncertain` and must preserve any known QMT order identity.

## Cancellation contract

Cancellation follows the same durable command identity rules. The sidecar may
return `Submitted`, `RejectedBeforeSend` or `Uncertain`; a terminal `Cancelled`
callback or complete authoritative snapshot resolves the economic/order state.
Order absence alone is not successful cancellation. If trade evidence shows the
order filled, the cancel resolves as target-terminal/filled, not successful cancel.

## Event normalization

Vendor callbacks are normalized into immutable events before leaving the sidecar.
Each event binds sidecar instance, connection epoch, account, full instrument,
QMT order ID/correlation, callback type and sidecar receive time. Trade events also
bind trade ID and positive finite quantity/price. Duplicate events are idempotent;
conflicting reuse of a trade ID or order correlation invalidates the affected
snapshot and enters recovery-required state.

Out-of-order status is accepted only through a monotonic state projection. A later
callback cannot regress a terminal order to active. `Filled` text without trade
or equivalent economic evidence does not by itself prove position change.

## Risk and final-send revalidation

The sidecar is not a portfolio risk authority. Execution owns the policy and final
decision. Immediately before vendor mutation, the XT binding must revalidate the
expected connection epoch, account, instrument mapping, market state, lot/tick
rules and quote identity/freshness under the transport send lock. If these inputs
changed after preview, reject before vendor entry. Agent-provided reference prices,
account names or market identifiers never replace service-owned values.

The first qualification scope should be deliberately narrow: one account, a finite
cash-equity universe, limit orders only, one currency and no credit/margin/options.
Adding ETFs, convertibles, futures, margin/credit or multiple accounts requires an
explicit risk/unit contract rather than reusing quantity as comparable exposure.

## Failure matrix

| Failure | Required behavior |
|---|---|
| sidecar unavailable before send | reject/unavailable, no external effect |
| malformed/oversized HXQ1 frame | close request, no mutation |
| stale service or connection epoch | reject before vendor call |
| request timeout after vendor entry | uncertain; same command only |
| Python/vendor exception after entry | uncertain |
| callback queue overflow | invalidate affected snapshots, fence risk |
| account mismatch | discard event, record conflict, fence if it touches owned order |
| duplicate exact trade callback | deduplicate |
| conflicting trade ID | recovery required |
| reconnect | increment epoch, clear completeness, full refresh |
| sidecar restart with unresolved send | command status + complete order/trade refresh before any risk increase |
| unsupported security/order type | explicit pre-send rejection |
| shutdown with active owner | terminal/recovery workflow; never silently abandon ownership |

## Observability

Fixed-cardinality runtime output must include sidecar instance presence,
connection epoch, connection state, account/position/order/trade refresh generation
and completeness, quote generation/age, callback queue depth/overflow count,
callback receive-to-normalization latency, HXQ1 request latency/result bins,
reconnect count/duration, unresolved command count and last bounded reason code.
Account IDs, order IDs, instrument symbols and command IDs are not metric labels.

## Development and qualification sequence

1. Acquire and hash the actual QMT/xtquant runtime; freeze the qualification
   profile and supported instrument/order subset.
2. Implement HXQ1 framing, mTLS peer identity and endpoint/firewall enforcement
   with hostile framing tests before importing vendor APIs.
3. Implement identity/health and read-only account/position/order/trade barriers;
   prove reconnect invalidation and known-empty semantics.
4. Implement quote subscription and freshness with deterministic callback fixtures.
5. Bind typed placement with durable Execution correlation and pre/post-vendor
   fault injection; same-ID retry must never duplicate a possible send.
6. Bind typed cancellation and terminal/trade reconciliation.
7. Add restart fixtures that retain unresolved correlations and require complete
   authoritative refresh before risk opens.
8. Package the exact sidecar/runtime identity, run a simulation/PAPER campaign,
   finish flat with no unresolved orders, and issue a digest-bound receipt.
9. Only then change capability advertising from experimental/false. LIVE remains a
   separate qualification decision.

## Acceptance evidence

Promotion from `EXPERIMENTAL` requires executed tests for framing, peer identity,
SDK-version mismatch, login/subscription state, every supported instrument/order
mapping, partial and duplicate fills, cancel races, queue overflow, disconnect at
each durable send boundary, sidecar/Execution restart, old command duplicate and
conflict, snapshot generation mixing, stale quote, account mismatch, terminal
reconciliation and final flatness. The real pinned QMT runtime must participate in
at least the qualification lane; synthetic callback fixtures alone cannot promote
transport or authorization status.
