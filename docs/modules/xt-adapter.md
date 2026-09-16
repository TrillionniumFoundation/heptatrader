# XT/QMT adapter

Status: EXPERIMENTAL  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_xt`
Tests: `tests/venue_capability_tests.cpp`, `tests/python/test_legacy_runtime_boundary.py`

## Current capability

The XT/QMT adapter is a minimal negative-capability boundary and still has no real XT transport. It must fail closed for connect, query, place, and cancel operations and must not emit synthetic accepted/submitted events that resemble broker evidence.

The earlier API mapping was based on a developer-local Python `xtquant` installation. That mapping is research input, not a pinned redistributable SDK contract.

## Selected next-venue implementation

XT/QMT is the selected next venue after the simulator and the bounded IB PAPER path. The implementation order and exact trust/protocol/state requirements are defined by the [XT/QMT execution-adapter contract](../technical/xtqmt-adapter-contract.md).

The deployment topology is no longer left ambiguous. Agent, Tool Gateway, OMS, risk and the durable Execution authority remain on the maintained Linux host. The pinned QMT/`xtquant` runtime runs in a dedicated Windows sidecar. Linux Execution reaches that one sidecar through the narrowly scoped HXQ1 protocol over mutually authenticated TLS 1.3 with pinned peer/host identity and explicit firewall policy. The Windows sidecar is a venue translator and callback normalizer, never a second command/risk authority.

Selection does not change capability truth. No pinned QMT installer, Python/xtquant package digest, Windows venue host, TLS profile, account runtime or qualification fixture exists in this repository today. Enabling a transport before those inputs and the cross-host boundary are implemented and qualified would be fabricated functionality. The current negative-capability source therefore remains the correct executable behavior.

## Intended responsibilities

The reviewed XT/QMT transport must provide:

- pinned Linux Execution and Windows QMT/xtquant artifact/host identities;
- TLS 1.3 mutual peer authentication, private endpoint and exact firewall isolation;
- bounded versioned HXQ1 framing with service, transport, sidecar and QMT connection epochs;
- account subscription and connection lifecycle;
- stable Execution-to-QMT order, cancel, trade and error correlations;
- authoritative asset, position, order, trade and quote refresh barriers;
- sequenced callback delivery with gap/overflow recovery;
- exchange-specific side, price-type, lot-size and market-state mapping;
- journaled uncertain-result recovery across network, sidecar, QMT and Execution restart;
- dedicated credentials, process identity and network policy;
- deterministic callback/transport fixtures plus actual pinned-QMT qualification.

A Python sidecar may never become a second order authority. Stable command identity, intent/send durability, risk, fencing and economic reconciliation remain Linux Execution responsibilities. A new TLS session or HXQ1 request ID is never permission to create another mutation identity.

## Failure semantics

Until a real transport is implemented and qualified, `Connect`, account/position/quote queries, order placement and cancellation return false. The reason is `XT_NOT_INITIALIZED` before valid initialization and `XT_TRANSPORT_NOT_IMPLEMENTED` afterwards. `Init` rejects a mode other than `XT` and resets initialized state. `IsConnected` is always false. Place clears an optional output order ID to zero and never manufactures acceptance.

Once transport work begins, TLS peer mismatch, stale transport/service/sidecar/QMT epoch or unsupported intent must reject before vendor mutation. Network loss after request delivery is `Uncertain` unless the authenticated sidecar can positively prove that vendor mutation entry never occurred. Any exception or ambiguous result after entering a vendor API is also `Uncertain`, not a definitive rejection. Reconnect invalidates the relevant transport or QMT epoch and snapshot completeness. Order absence is not cancellation proof, and a Filled status without trade/economic evidence cannot by itself manufacture position truth. The technical contract owns the detailed matrix.

## Observability and tests

The scaffold reports its capability status and last failure reason. The current test verifies that it remains disconnected and rejects mutation. Promotion requires real TLS peer/endpoint isolation tests, framing/epoch tests, callback ordering and gap recovery, duplicate/conflicting event tests, partial fill, cancel race, network/QMT reconnect, account mismatch, invalid price type, stale quote, uncertain send, Windows-sidecar and Linux-Execution restart, old-command duplicate/conflict and final reconciliation evidence as enumerated by the technical contract.

## Deliberately minimal source surface

The retired monolith was the old callback consumer. A whole-tree consumer search found only this scaffold and `venue_capability_tests.cpp` using its callback/event API. Empty economic callbacks, a diagnostic event queue and configuration pretending to supply account/session/risk policy have therefore been removed rather than retained solely for tests of an unimplemented venue. There is no pinned SDK, deployed transport or persistent vendor format behind them.

The negative-capability methods remain to reject accidental venue selection. Tests execute repeated requests, unsupported numeric inputs and invalid-mode reinitialization; all remain disconnected with no order ID or send. New code should implement the reviewed cross-host HXQ1 contract against the actual pinned QMT runtime rather than revive speculative callbacks or introduce a generic REST/TCP bridge. External users of the former experimental C++ callback API must migrate explicitly; no universal source compatibility is claimed. CTP remains an unselected experimental scaffold and similarly must not manufacture transport state. No current OMS/HSL reader or authority guard is removed by this development choice.
