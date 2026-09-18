# XT/QMT adapter

Status: EXPERIMENTAL  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_xt`
Tests: `tests/venue_capability_tests.cpp`, `tests/python/test_legacy_runtime_boundary.py`

## Current capability

The XT/QMT source now implements the exact **HXQ1 v1 read-only client boundary**, but not the production Windows/QMT transport. `HeptaXTGatewayAdapter` can frame and validate bounded HXQ1 messages, bind account/service epoch/connection epoch plus a trusted account currency and finite authorized instrument universe, perform an identity handshake, and decode strict typed `account_snapshot`, `position_snapshot`, `order_snapshot`, `trade_snapshot`, and `quote_subscribe` payloads through an explicitly injected **already-admitted read-only exchange**. Account/position has its existing same-generation barrier; the stricter account+position+order+trade barrier additionally requires one shared non-zero generation and validates trade-to-order identity/instrument/side linkage. Identity handshake alone is only `XT_READ_ONLY_CONNECTED`.

That injected exchange is deliberately not a generic socket or Agent callback. Production wiring must supply a peer-pinned mTLS channel owned by the Execution identity. The repository does not claim that the QMT sidecar, certificates, Windows firewall policy, pinned `xtquant` runtime, or target account exists merely because the protocol client is executable.

Mutation remains unavailable. `place` and `cancel` are not routed through the read-only exchange and always fail closed; an injected transport therefore cannot accidentally turn protocol development into trading authority.

## Selected next-venue implementation

XT/QMT is the selected next venue after the simulator and the bounded IB PAPER path. The exact trust, protocol, state and qualification requirements are defined by the [XT/QMT execution-adapter contract](../technical/xtqmt-adapter-contract.md). The selected topology keeps canonical Execution authority on Linux and reaches a dedicated Windows Python/QMT sidecar through one pinned mutually authenticated HXQ1/TLS trust domain. Agent and Gateway cannot reach that listener. Execution remains the sole durable order authority.

Current source work is intentionally staged:

1. **Implemented now:** bounded HXQ1 v1 framing, exact response binding, account/service/connection-epoch identity, read-only handshake, strict account/position/order/trade/quote payload decoding, profile-bound account currency and finite instrument-universe admission, completed known-empty arrays, same-generation account/position and account/position/order/trade barriers, trade-to-order correlation checks, per-instrument quote authority for the bounded profile universe, Execution-clock quote freshness, transport/peer-response ambiguity invalidation of the connection and every cached authority family, hostile/mismatched/economically-invalid response rejection, disconnect invalidation, and hard mutation disablement.
2. **Still external/unimplemented:** the real mTLS channel, Windows sidecar process/instance identity and health, pinned QMT/Python/`xtquant` inputs, firewall/credential policy, callback normalization and target-host qualification.
3. **Future mutation stage:** only after read-only qualification, add durable `venue_command_id` mutation correlation and uncertain-send recovery. No mutation method is enabled by the current work.

## Read-only protocol behavior

HXQ1 frames are a four-byte unsigned big-endian payload length followed by one canonical UTF-8 JSON object, with a maximum JSON payload of 256 KiB. The current client binds every request and response to `protocol=HXQ1`, `version=1`, request ID, Execution service epoch, XT/QMT connection epoch, operation and qualification account. A response with a changed request ID, epoch, operation, account, framing length, or extra/noncanonical content is rejected rather than interpreted loosely.

The current C++ adapter exposes identity plus typed account, position, order, trade and quote read operations. Quote authority is retained independently per authorized instrument rather than overwriting the prior instrument when another subscription is refreshed. Position, order, trade and quote instruments must belong to the trusted finite profile universe before they can become cached authority. Order rows bind stable normalized order identity, instrument, side, bounded lifecycle status, quantity/fill and LMT price; trade rows bind stable trade+order identities, instrument/side, positive quantity/price and occurrence time. Completed order/trade arrays use the same connection epoch and generation rules as account/position, and `AccountPositionOrderTradeReadReady()` rejects a trade that cannot be linked to the matching order identity/instrument/side or a filled order with no trade evidence. Quote freshness is evaluated only against an Execution-owned evaluation time and explicit maximum age. These schemas use exact canonical field order and JSON-number grammar and reject extra/duplicate/noncanonical alternatives. `venue_command_id` remains empty on read operations and is reserved for future durable mutation correlation.

This is still not full venue `READY`: sidecar instance identity/health, real transport admission and real QMT-produced snapshots are not implemented. The explicit readiness methods intentionally name only the source subsets they can prove.

## Failure semantics

Before valid initialization, requests fail with `XT_NOT_INITIALIZED`. With no admitted read-only exchange, `Connect()` fails with `XT_TRANSPORT_NOT_IMPLEMENTED` and capability remains `EXPERIMENTAL_NO_TRANSPORT`. A fully bound read-only configuration requires a trusted account currency and a non-empty bounded authorized instrument set and reports `EXPERIMENTAL_READ_ONLY_HXQ1`; identity handshake failure remains fail closed. After a successful handshake, an admitted-channel failure, malformed response frame or response-binding ambiguity invalidates the connection and clears account/position/order/trade plus all per-instrument quote authority; a new identity handshake is required before reads resume. Explicit disconnect has the same authority-erasure property and subsequent reads fail with `XT_READ_ONLY_NOT_CONNECTED`.

`PlaceOrder` and `CancelOrder` never use HXQ1 in this stage. If the read-only transport is configured they fail with `XT_MUTATION_DISABLED`; without a transport they retain `XT_TRANSPORT_NOT_IMPLEMENTED`. No local order ID or accepted/submitted event is manufactured.

## Security boundary

The Windows sidecar remains a future dedicated service identity. It must receive no Agent session token and cannot mint command IDs, decision leases or Execution owner generations. The eventual mTLS wrapper must pin the allowed Linux host, peer certificate/public-key identity, qualification profile and message bounds before supplying the admitted exchange to this adapter.

A Python sidecar may never become a second order authority. Stable command identity, intent/send durability, risk, fencing and economic reconciliation remain Execution responsibilities.

## Observability and tests

`venue_capability_tests.cpp` covers both negative capability and the executable read-only boundary: exact length framing, identity/account/epoch binding, typed account/position/order/trade/quote payloads, completed known-empty positions, same-generation barriers, trade/order correlation, quote freshness boundaries, non-JSON numeric spellings, duplicate identities, mixed-generation refusal, economically invalid payload rejection, untrusted currency/instrument rejection before authority caching, independent multi-instrument quote caching, transport-failure connection/authority invalidation, reconnect-without-stale-authority behavior, disconnect state erasure, mismatched response rejection, and proof that read-only connectivity still cannot place or cancel an order.

Promotion beyond `EXPERIMENTAL` still requires real mTLS peer tests, QMT callback ordering, duplicate/conflicting events, partial fills, cancel races, reconnect, account mismatch, invalid price type, stale quote, uncertain send, sidecar restart, old-command duplicate/conflict and final reconciliation evidence against the pinned runtime.

## CTP priority

CTP is explicitly deferred while XT/QMT is the selected next venue. Its existing negative-capability scaffold remains only to reject accidental selection; no parallel speculative CTP transport layer should be expanded until the XT read-only and qualification path has converged.
