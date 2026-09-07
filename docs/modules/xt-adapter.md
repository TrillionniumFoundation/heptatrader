# XT/QMT adapter

Status: EXPERIMENTAL  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_xt/`  
Tests: `tests/venue_capability_tests.cpp`

## Current capability

The XT/QMT adapter stabilizes a prospective event and configuration shape, but it has no real XT transport. It must fail closed for connect, query, place, and cancel operations and must not emit synthetic accepted/submitted events that resemble broker evidence.

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

Until the real transport is installed and qualified, `Connect`, account/position/quote queries, order placement, and cancellation return false with an `XT_TRANSPORT_NOT_IMPLEMENTED` reason. No method may manufacture a broker order ID or submitted status.

## Observability and tests

The scaffold reports its capability status and last failure reason. The current test verifies that it remains disconnected and rejects mutation. A future implementation requires callback ordering, duplicate event, partial fill, reconnect, account mismatch, invalid price type, and uncertain send tests before status promotion.
