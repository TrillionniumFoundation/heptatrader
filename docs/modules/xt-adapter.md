# XT/QMT adapter

Status: EXPERIMENTAL  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_xt`
Tests: `tests/venue_capability_tests.cpp`, `tests/python/test_legacy_runtime_boundary.py`

## Current capability

The XT/QMT source now implements the exact **HXQ1 v1 read-only client boundary**, but not the production Windows/QMT transport. `HeptaXTGatewayAdapter` can frame and validate bounded HXQ1 messages, bind account/service epoch/connection epoch, perform an identity handshake, and decode strict typed `account_snapshot`, `position_snapshot`, and `quote_subscribe` payloads through an explicitly injected **already-admitted read-only exchange**. Account/position state is exposed as ready only when both completed snapshots share the configured connection epoch and the same non-zero snapshot generation; identity handshake alone is only `XT_READ_ONLY_CONNECTED`.

That injected exchange is deliberately not a generic socket or Agent callback. Production wiring must supply a peer-pinned mTLS channel owned by the Execution identity. The repository does not claim that the QMT sidecar, certificates, Windows firewall policy, pinned `xtquant` runtime, or target account exists merely because the protocol client is executable.

Mutation remains unavailable. `place` and `cancel` are not routed through the read-only exchange and always fail closed; an injected transport therefore cannot accidentally turn protocol development into trading authority.

## Selected next-venue implementation

XT/QMT is the selected next venue after the simulator and the bounded IB PAPER path. The exact trust, protocol, state and qualification requirements are defined by the [XT/QMT execution-adapter contract](../technical/xtqmt-adapter-contract.md). The selected topology keeps canonical Execution authority on Linux and reaches a dedicated Windows Python/QMT sidecar through one pinned mutually authenticated HXQ1/TLS trust domain. Agent and Gateway cannot reach that listener. Execution remains the sole durable order authority.

Current source work is intentionally staged:

1. **Implemented now:** bounded HXQ1 v1 framing, exact response binding, account/service/connection-epoch identity, read-only handshake, strict account/position/quote payload decoding, completed known-empty positions, same-generation account/position barrier, hostile/mismatched/economically-invalid response rejection, disconnect invalidation, and hard mutation disablement.
2. **Still external/unimplemented:** the real mTLS channel, Windows sidecar process, pinned QMT/Python/`xtquant` inputs, firewall/credential policy, callback normalization and target-host qualification.
3. **Future mutation stage:** only after read-only qualification, add durable `venue_command_id` mutation correlation and uncertain-send recovery. No mutation method is enabled by the current work.

## Read-only protocol behavior

HXQ1 frames are a four-byte unsigned big-endian payload length followed by one canonical UTF-8 JSON object, with a maximum JSON payload of 256 KiB. The current client binds every request and response to `protocol=HXQ1`, `version=1`, request ID, Execution service epoch, XT/QMT connection epoch, operation and qualification account. A response with a changed request ID, epoch, operation, account, framing length, or extra/noncanonical content is rejected rather than interpreted loosely.

The current C++ adapter exposes only the read operations that already have a defined first-stage consumer surface: identity, account snapshot, position snapshot and quote subscription. Account payloads carry one currency plus finite non-negative cash/asset/available-cash values; position payloads carry a bounded unique instrument set with finite non-negative quantity/sellable quantity/cost and explicit completed empty sets; quote payloads bind instrument, generation, bid/ask and observation time. These schemas use exact canonical field order and reject extra/duplicate/noncanonical alternatives rather than accepting a permissive JSON variant. `venue_command_id` is empty on these read operations; it remains reserved for future durable mutation correlation.

This is still not full venue `READY`: order/trade refresh barriers, sidecar instance identity and real transport admission are not implemented. `AccountPositionReadReady()` intentionally names only the source subset it can prove.

## Failure semantics

Before valid initialization, requests fail with `XT_NOT_INITIALIZED`. With no admitted read-only exchange, `Connect()` fails with `XT_TRANSPORT_NOT_IMPLEMENTED` and capability remains `EXPERIMENTAL_NO_TRANSPORT`. A fully bound read-only configuration reports `EXPERIMENTAL_READ_ONLY_HXQ1`; identity handshake failure, framing failure or response-binding mismatch remains fail closed. After a successful handshake, disconnect clears connected state and subsequent reads fail with `XT_READ_ONLY_NOT_CONNECTED`.

`PlaceOrder` and `CancelOrder` never use HXQ1 in this stage. If the read-only transport is configured they fail with `XT_MUTATION_DISABLED`; without a transport they retain `XT_TRANSPORT_NOT_IMPLEMENTED`. No local order ID or accepted/submitted event is manufactured.

## Security boundary

The Windows sidecar remains a future dedicated service identity. It must receive no Agent session token and cannot mint command IDs, decision leases or Execution owner generations. The eventual mTLS wrapper must pin the allowed Linux host, peer certificate/public-key identity, qualification profile and message bounds before supplying the admitted exchange to this adapter.

A Python sidecar may never become a second order authority. Stable command identity, intent/send durability, risk, fencing and economic reconciliation remain Execution responsibilities.

## Observability and tests

`venue_capability_tests.cpp` covers both negative capability and the executable read-only boundary: exact length framing, identity/account/epoch binding, typed account/position/quote payloads, completed known-empty positions, same-generation readiness, mixed-generation refusal, economically invalid payload rejection, disconnect state erasure, mismatched response rejection, and proof that read-only connectivity still cannot place or cancel an order.

Promotion beyond `EXPERIMENTAL` still requires real mTLS peer tests, QMT callback ordering, duplicate/conflicting events, partial fills, cancel races, reconnect, account mismatch, invalid price type, stale quote, uncertain send, sidecar restart, old-command duplicate/conflict and final reconciliation evidence against the pinned runtime.

## CTP priority

CTP is explicitly deferred while XT/QMT is the selected next venue. Its existing negative-capability scaffold remains only to reject accidental selection; no parallel speculative CTP transport layer should be expanded until the XT read-only and qualification path has converged.
