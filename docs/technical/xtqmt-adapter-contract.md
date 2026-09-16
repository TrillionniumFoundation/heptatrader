# XT/QMT execution-adapter implementation contract

Status: PROPOSAL
Applies to: selected next venue after the canonical simulator and qualified IB PAPER path
Owner boundary: Linux Execution Service remains the sole durable order authority

## Why XT/QMT is the selected next adapter

The repository already carries an explicit XT/QMT negative-capability boundary and historical `xtquant` mapping research. CTP requires a separate native vendor SDK, front/login/settlement state and exchange-specific close-today semantics before even a read-only authority can be assembled. XT/QMT is therefore the narrower next integration target.

The supported topology is now explicit rather than leaving a local/remote contradiction unresolved: the maintained Agent, Tool Gateway, OMS, risk and Execution authority stay on the Linux trading host; the QMT/`xtquant` runtime stays on a dedicated Windows venue host; the two are joined by one mutually authenticated, explicitly allow-listed HXQ1 transport. The Windows process is a venue sidecar, not a second execution authority.

This selection is a development order, not a claim that XT transport exists or is licensed, installed, qualified or authorized. Until the pinned vendor runtime, host identity and transport credentials below are supplied and every acceptance stage passes, `xt-adapter` remains `EXPERIMENTAL`, `transport_implemented=false`, `advertisable=false` and every mutation fails closed.

## Resolved deployment topology and trust boundary

```text
Agent / MCP / CLI
       |
       | local HTT1 / Unix authority boundary
       v
Tool Gateway (Linux)
       |
       | local HEX1
       v
Execution Service (Linux) ---- durable OMS / risk / fencing
       |
       | HXQ1 v1 over mutually authenticated TLS 1.3
       | fixed Windows venue-host identity + pinned private address
       v
XT/QMT Sidecar (Windows)
       |
       | local pinned xtquant / QMT runtime
       v
QMT client
```

The Linux Execution Service remains the only component allowed to create durable mutation identity, decide final risk, append `intent + send_attempt`, fence owners, classify an unresolved mutation and reconcile economic truth. The Windows sidecar may translate one already-authorized venue command into vendor calls and normalize vendor callbacks. It cannot mint command IDs, decision leases, Agent sessions or portfolio policy.

This cross-host link is a new trust domain and is therefore deliberately narrower than a generic remote bridge:

- one configured Linux Execution host identity and one configured Windows sidecar identity;
- TLS 1.3 with mutual certificate authentication; both leaf/SPKI identities are bound by the qualification profile rather than public-Web PKI discovery;
- a private, explicitly configured endpoint; no DNS-based service discovery, public listener or arbitrary peer fallback;
- host firewalls allow only the exact Execution-to-sidecar flow; Agent/Gateway identities have no route or credential for HXQ1 or QMT;
- the sidecar reaches only its local reviewed QMT endpoint and exposes no raw Python/vendor-call RPC;
- certificate/key material is delivered by host credential facilities and never packaged in the repository or release artifact;
- transport reconnect creates a new HXQ1 transport epoch; QMT reconnect creates a new venue connection epoch; neither can inherit READY state from its predecessor.

A plaintext LAN socket, SSH command tunnel, generic REST bridge, shared Windows desktop automation channel or Internet-reachable endpoint is not a supported implementation shortcut. Changing this topology requires a new architecture/security contract rather than a configuration toggle.

## Pinned external inputs

Before transport code is enabled, one qualification profile must bind all of:

- Linux Execution artifact/executable digest and host identity;
- Windows sidecar executable/source digest and host identity;
- TLS trust roots or exact peer certificate/SPKI digests, endpoint address/port and firewall-policy digest;
- QMT client product/version and installer digest;
- Python interpreter implementation/version and executable digest;
- `xtquant` package/version and complete installed-package digest;
- account type and a simulation/PAPER-only account identifier for first qualification;
- QMT data directory and session namespace;
- allowed exchange/security/order-price-type matrix;
- exact instrument identity mapping and lot/tick rules;
- qualification fixture/harness digest.

A developer-local import, workstation certificate store default, unpinned host address or manually opened firewall is not a supported SDK/transport profile. Missing or changed inputs keep the adapter in negative-capability mode and require a new qualification campaign.

## HXQ1 transport protocol

HXQ1 is versioned independently of Agent `HTT1`, Execution `HEX1` and supervisor `HSS1`. Version 1 runs only inside the mutually authenticated transport above. Frames are length-prefixed, bounded to 256 KiB and contain one canonical UTF-8 JSON object; duplicate keys, non-finite numbers, unknown mutation fields and invalid lengths are rejected before dispatch.

The TLS session is transport authentication, not mutation identity. Every application request also carries explicit epochs and stable Execution correlation:

| Field | Requirement |
|---|---|
| `protocol` | exact `HXQ1` |
| `version` | integer `1` |
| `request_id` | per-transport request ID; never the durable mutation identity |
| `service_epoch` | current Linux Execution service epoch |
| `transport_epoch` | current authenticated HXQ1 connection epoch |
| `sidecar_instance_id` | boot/process identity advertised by the authenticated sidecar |
| `connection_epoch` | expected QMT venue connection epoch |
| `operation` | one of the operations below |
| `account` | exact qualification-bound account |
| `venue_command_id` | Execution-generated stable correlation derived from durable owner/session/command identity |
| `payload` | operation-specific bounded object |

The sidecar rejects a stale service, transport, instance or venue epoch before entering a vendor mutation API. `venue_command_id` remains stable across response-loss recovery. Reusing it with a different normalized payload is a protocol conflict. A new `request_id`, TLS session or Windows process never authorizes a second economic mutation.

For mutation commands, the sidecar must durably retain or reconstruct a bounded correlation record before it can report `Submitted`: `venue_command_id`, normalized request hash, account, QMT connection epoch, sidecar instance, vendor request/order identity and disposition. After sidecar restart, status/reconciliation is required before any ambiguous command can be retried.

### Operations

Read operations: `identity`, `health`, `account_snapshot`, `position_snapshot`, `order_snapshot`, `trade_snapshot`, `quote_subscribe`, `quote_unsubscribe`, `command_status`.

Mutation operations: `place`, `cancel`. Authoritative flatten remains a Linux Execution plan expressed as an exact reduce-only `place`; there is no sidecar-owned flatten policy and no generic raw vendor-call operation.

## Connection and authority state machine

Two epochs are tracked separately: HXQ1 transport identity and QMT venue identity.

```text
DISABLED
  -> TLS_AUTHENTICATED
  -> SIDECAR_IDENTIFIED
  -> QMT_CONNECTED
  -> ACCOUNT_SUBSCRIBED
  -> REFRESHING
  -> READY

any transport/account/session ambiguity
  -> DEGRADED / RECOVERY_REQUIRED
  -> TLS_AUTHENTICATED / REFRESHING only through newer epochs

shutdown
  -> DRAINING
  -> CLOSED
```

`READY` requires the expected mutually authenticated peer, exact sidecar instance, exact account subscription plus complete asset, position, order and trade barriers for the same QMT connection epoch. Quote completeness is tracked per instrument/subscription generation. TCP/TLS success, Python import or QMT process presence alone is never `READY`.

Any HXQ1 reconnect changes `transport_epoch` and prevents old replies from being admitted to a new stream. Any sidecar restart changes `sidecar_instance_id`. Any QMT reconnect increments `connection_epoch`, invalidates quote and account/order/trade snapshot completeness and fences risk increase until authoritative barriers are rebuilt. An unresolved mutation spanning any of those changes remains uncertain until command status plus complete order/trade reconciliation resolves it.

## Instrument and order identity

The initial instrument key is the full QMT security identity, not a display symbol: `market + stock_code + security_type`. The supported first qualification universe is finite and source-controlled. Mapping to any canonical HeptaTrader instrument includes exchange, currency, lot size, price tick and security type.

Order correlation retains all available identities:

- Execution `venue_command_id` and normalized request hash;
- QMT account;
- Linux service epoch and HXQ1 transport epoch;
- Windows sidecar instance ID;
- QMT connection epoch;
- client/order request sequence when available;
- QMT order ID;
- exchange/order-system ID when exposed;
- trade IDs for economic executions.

A numeric QMT order ID without matching account/epoch/instrument provenance is not sufficient to resolve an uncertain send.

## Authoritative refresh barriers

Each refresh has a sidecar-generated monotonically increasing generation bound to one QMT connection epoch and one sidecar instance. A snapshot is complete only after the API's corresponding query terminates successfully and every row passes identity/numeric validation.

Required first-scope snapshots:

- asset/account: cash, total asset and available cash with explicit currency;
- positions: full security identity, quantity, sellable quantity and cost fields;
- orders: all active and terminal orders needed for command recovery;
- trades: trade ID, order correlation, side, quantity, price and timestamp;
- quotes: bid/ask, observation time, subscription identity and freshness bound.

Partial callback batches, query exceptions, account mismatch, unsupported security fields or duplicate conflicting keys make the generation incomplete. Known-empty is represented by a completed empty snapshot, not by timeout or missing callbacks. Linux Execution accepts a completed snapshot only if its peer, service/transport/sidecar/connection epochs and account binding still match the current authority state.

## Placement contract

Linux Execution performs owner/session/lease validation, stable command hashing, final deterministic risk and durable `intent + send_attempt` before transmitting HXQ1 `place`.

The sidecar returns one typed disposition:

| Disposition | Required evidence | Execution treatment |
|---|---|---|
| `Submitted` | authenticated request reached the expected sidecar; vendor call returned accepted entry; durable sidecar correlation is retained | await authoritative order/trade callback; not an economic fill |
| `RejectedBeforeSend` | validation failed before vendor mutation entry | durable rejection is permitted |
| `Uncertain` | vendor mutation entry occurred or cannot be excluded, and response/correlation is incomplete | keep original command ID, fence risk and reconcile |

TLS/HXQ1 loss before the sidecar positively proves that vendor mutation entry did not occur may be a transport rejection. Loss after request delivery but before such proof is `Uncertain`; network failure must not be converted to `RejectedBeforeSend` merely because Linux did not receive a response.

A Python/vendor exception after entering placement is `Uncertain`. Boolean false after vendor entry is not automatically a no-send rejection unless the pinned SDK contract provides an independently verifiable pre-send guarantee for that exact path. Allocation, sidecar persistence or response-serialization failure after possible vendor acceptance is also `Uncertain` and preserves every known vendor identity.

## Cancellation contract

Cancellation follows the same durable command-identity and network-ambiguity rules. The sidecar may return `Submitted`, `RejectedBeforeSend` or `Uncertain`; a positive terminal `Cancelled` callback or complete authoritative snapshot resolves the target state. Order absence alone is not successful cancellation. If trade evidence shows the order filled, cancellation resolves as target-terminal/filled, not as successful cancel.

## Event normalization and streaming

Vendor callbacks are normalized into immutable HXQ1 events before leaving Windows. Each event binds sidecar instance, HXQ1 transport epoch, QMT connection epoch, account, full instrument, QMT order correlation, callback type and sidecar receive time. Trade events also bind trade ID and positive finite quantity/price.

The sidecar assigns a monotonic event sequence within one sidecar instance/venue epoch. Linux acknowledges the highest contiguous sequence it has durably/projectably consumed. A reconnect resumes from the last retained event/correlation state when supported; otherwise Linux requires complete authoritative refresh before risk can reopen. An event gap, queue overflow or sequence regression invalidates affected snapshots and enters recovery-required state.

Duplicate exact events are idempotent. Conflicting reuse of a trade ID or order correlation invalidates the affected snapshot. Out-of-order status is accepted only through a monotonic state projection; a later callback cannot regress a terminal order to active. `Filled` text without trade/economic evidence does not by itself prove position change.

## Risk and final-send revalidation

The sidecar is not portfolio risk authority. Linux Execution owns the decision. Immediately before vendor mutation, the sidecar performs only venue-binding revalidation: current authenticated Linux peer/service epoch, transport/sidecar/QMT epochs, account, instrument mapping, market state, lot/tick rules and the quote identity/freshness supplied through the reviewed venue contract. If any changed after Linux approval, reject before vendor entry.

Agent-provided reference prices, account names or market identifiers never replace service-owned values. The first qualification scope is deliberately narrow: one account, a finite cash-equity universe, limit orders only, one currency and no credit/margin/options. ETFs, convertibles, futures, margin/credit or multiple accounts require explicit unit/risk contracts rather than reusing quantity as comparable exposure.

## Failure matrix

| Failure | Required behavior |
|---|---|
| TLS peer/certificate mismatch | refuse connection; no venue request |
| sidecar unavailable before request delivery | unavailable/reject, no external effect |
| malformed/oversized HXQ1 | close request/connection, no mutation |
| stale service/transport/sidecar/QMT epoch | reject before vendor call |
| network timeout after request delivery | uncertain unless sidecar proves pre-vendor rejection |
| Python/vendor exception after entry | uncertain |
| sidecar correlation persistence failure after possible send | uncertain; preserve known identities |
| event sequence gap / callback queue overflow | invalidate affected snapshots, fence risk |
| account mismatch | discard event, record conflict, fence if it touches an owned command |
| duplicate exact trade callback | deduplicate |
| conflicting trade ID | recovery required |
| TLS reconnect | new transport epoch; reject stale responses; reconcile unresolved commands |
| QMT reconnect | increment venue epoch, clear completeness, full refresh |
| sidecar restart with unresolved send | command status + complete order/trade refresh before risk increase |
| unsupported security/order type | explicit pre-send rejection |
| shutdown with active owner | terminal/recovery workflow; never silently abandon ownership |

## Observability

Fixed-cardinality runtime output must include authenticated peer presence, transport epoch/state, sidecar instance presence, QMT connection epoch/state, account/position/order/trade refresh generation and completeness, quote generation/age, event sequence lag/gap, callback queue depth/overflow count, callback receive-to-normalization latency, HXQ1 request latency/result bins, reconnect count/duration, unresolved-command count and bounded reason codes.

Account IDs, order IDs, instrument symbols, command IDs, certificate fingerprints and IP addresses are not metric labels. Exact peer/artifact/certificate identities belong in bounded qualification/diagnostic receipts, not cardinality-unbounded metrics.

## Development and qualification sequence

1. Acquire and hash the actual QMT/xtquant runtime; freeze the Windows version, account type and finite first instrument/order subset.
2. Freeze the Linux↔Windows trust profile: exact hosts, private endpoint, certificate/SPKI identities and firewall policy. Prove Agent/Gateway cannot reach HXQ1/QMT.
3. Implement TLS 1.3 mutual authentication, HXQ1 framing/epoch/peer checks and hostile transport tests before importing vendor APIs.
4. Implement sidecar identity/health and read-only account/position/order/trade barriers; prove TLS/QMT reconnect invalidation and known-empty semantics.
5. Implement quote subscription/freshness plus sequenced callback streaming with deterministic fixtures, gap/overflow recovery and complete-refresh fallback.
6. Bind typed placement to the already-durable Linux command identity with pre/post-delivery and pre/post-vendor fault injection; same-ID recovery must never duplicate a possible send.
7. Bind typed cancellation and terminal/trade reconciliation under the same uncertainty rules.
8. Add Linux and Windows restart fixtures retaining unresolved correlations and requiring complete authoritative refresh before risk reopens.
9. Package and bind both exact artifacts plus transport/vendor identities; run an isolated simulation/PAPER qualification campaign, finish flat with no unresolved commands and issue a digest-bound receipt.
10. Only then change capability advertising from experimental/false. LIVE remains a separate qualification decision.

## Acceptance evidence

Promotion from `EXPERIMENTAL` requires executed evidence for TLS peer identity and certificate rotation/rejection, endpoint/firewall isolation, framing, stale epochs, SDK-version mismatch, account subscription, every supported instrument/order mapping, partial/duplicate fills, cancel races, event sequence gaps, disconnect at each durable/network/vendor boundary, Windows sidecar restart, Linux Execution restart, old command duplicate/conflict, snapshot generation mixing, stale quote, account mismatch, terminal reconciliation and final flatness.

The real pinned QMT/xtquant runtime must participate in the qualification lane. Synthetic callbacks, successful TLS handshakes or source-only CI cannot promote transport or authorization status.
