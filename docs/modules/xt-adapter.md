# XT/QMT adapter

Status: EXPERIMENTAL  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_xt`
Tests: `tests/venue_capability_tests.cpp`

## Current capability

The XT/QMT adapter is a minimal negative-capability boundary, not a prospective SDK or event model. It has no real XT transport. It must fail closed for connect, query, place, and cancel operations and must not emit synthetic accepted/submitted events that resemble broker evidence.

The earlier API mapping was based on a developer-local Python `xtquant` installation. That mapping is research input, not a pinned redistributable SDK contract.

## Intended responsibilities

A future adapter may bridge a separately reviewed XT/QMT transport into the common Execution venue contract. It must provide:

- a versioned SDK/protocol boundary and supported platform matrix;
- account subscription and connection lifecycle;
- stable order, cancel, trade, and error correlations;
- authoritative asset, position, order, trade, and quote refresh barriers;
- exchange-specific side, price-type, lot-size, and market-state mapping;
- journaled uncertain-result recovery;
- dedicated credentials, process identity, and network policy;
- deterministic callback fixtures and broker-observed qualification.

A Python sidecar is acceptable only if its local IPC protocol, package digest, interpreter/runtime, permissions, crash semantics, and upgrade policy are explicit. The sidecar may not become a second order authority.

## Failure semantics

Until a real transport is implemented and qualified, `Connect`, account/position/quote queries, order placement and cancellation return false. The reason is `XT_NOT_INITIALIZED` before valid initialization and `XT_TRANSPORT_NOT_IMPLEMENTED` afterwards. `Init` rejects a mode other than `XT` and resets initialized state. `IsConnected` is always false. Place clears an optional output order ID to zero and never manufactures acceptance.

## Observability and tests

The scaffold reports its capability status and last failure reason. The current test verifies that it remains disconnected and rejects mutation. A future implementation requires callback ordering, duplicate event, partial fill, reconnect, account mismatch, invalid price type, and uncertain send tests before status promotion.

## Deliberately minimal source surface

The retired monolith was the old callback consumer. A whole-tree consumer
search found only this scaffold and `venue_capability_tests.cpp` using its
callback/event API. Empty economic callbacks, a diagnostic event queue and
configuration pretending to supply account/session/risk policy have therefore
been removed, rather than retained solely for tests of an unimplemented venue.
There is no pinned SDK, deployed transport or persistent format behind them.

The negative-capability methods remain to reject accidental venue selection.
Tests execute repeated requests, unsupported numeric inputs and invalid-mode
reinitialization; all remain disconnected with no order ID or send. A future
transport must introduce its real SDK contract and behavior tests rather than
revive these speculative placeholders. External users of the former experimental
C++ callback API must migrate explicitly; no universal source compatibility is
claimed. CTP similarly no longer stores an unused configuration copy or mutable
connected flag. No current OMS/HSL reader or authority guard was removed.
