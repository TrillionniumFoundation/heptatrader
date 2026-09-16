# XT/QMT adapter

Status: EXPERIMENTAL  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_xt`
Tests: `tests/venue_capability_tests.cpp`, `tests/python/test_legacy_runtime_boundary.py`

## Current capability

The XT/QMT adapter is a minimal negative-capability boundary and still has no real XT transport. It must fail closed for connect, query, place, and cancel operations and must not emit synthetic accepted/submitted events that resemble broker evidence.

The earlier API mapping was based on a developer-local Python `xtquant` installation. That mapping is research input, not a pinned redistributable SDK contract.

## Selected next-venue implementation

XT/QMT is now the selected next venue after the simulator and the bounded IB PAPER path. The implementation order and exact trust/protocol/state requirements are defined by the [XT/QMT execution-adapter contract](../technical/xtqmt-adapter-contract.md). The intended first integration uses a dedicated local Python/QMT sidecar so the vendor runtime does not enter Agent, Gateway or shared coordinator processes. Execution remains the sole durable order authority.

Selection does not change capability truth. No pinned QMT installer, Python/xtquant package digest, account runtime or qualification fixture exists in this repository today, so enabling a transport before those inputs are supplied would be fabricated functionality. The current negative-capability source therefore remains the correct executable behavior until the contract's first SDK-custody stage can be implemented and tested.

## Intended responsibilities

The reviewed XT/QMT transport must provide:

- a pinned SDK/runtime boundary and supported platform matrix;
- a local versioned IPC protocol with explicit peer/identity custody;
- account subscription and connection lifecycle;
- stable Execution-to-QMT order, cancel, trade and error correlations;
- authoritative asset, position, order, trade and quote refresh barriers;
- exchange-specific side, price-type, lot-size and market-state mapping;
- journaled uncertain-result recovery across sidecar and Execution restart;
- dedicated credentials, process identity and network policy;
- deterministic callback fixtures plus actual pinned-QMT qualification.

A Python sidecar may never become a second order authority. Stable command identity, intent/send durability, risk, fencing and economic reconciliation remain Execution responsibilities.

## Failure semantics

Until a real transport is implemented and qualified, `Connect`, account/position/quote queries, order placement and cancellation return false. The reason is `XT_NOT_INITIALIZED` before valid initialization and `XT_TRANSPORT_NOT_IMPLEMENTED` afterwards. `Init` rejects a mode other than `XT` and resets initialized state. `IsConnected` is always false. Place clears an optional output order ID to zero and never manufactures acceptance.

Once transport work begins, any exception or ambiguous result after entering a vendor mutation API is `Uncertain`, not a definitive rejection. Reconnect changes the connection epoch and invalidates snapshot completeness. Order absence is not cancellation proof, and a Filled status without trade/economic evidence cannot by itself manufacture position truth. The technical contract owns the detailed failure matrix.

## Observability and tests

The scaffold reports its capability status and last failure reason. The current test verifies that it remains disconnected and rejects mutation. Promotion requires real framing/peer tests, callback ordering, duplicate/conflicting event tests, partial fill, cancel race, reconnect, account mismatch, invalid price type, stale quote, uncertain send, sidecar restart, old-command duplicate/conflict and final reconciliation evidence as enumerated by the technical contract.

## Deliberately minimal source surface

The retired monolith was the old callback consumer. A whole-tree consumer search found only this scaffold and `venue_capability_tests.cpp` using its callback/event API. Empty economic callbacks, a diagnostic event queue and configuration pretending to supply account/session/risk policy have therefore been removed, rather than retained solely for tests of an unimplemented venue. There is no pinned SDK, deployed transport or persistent format behind them.

The negative-capability methods remain to reject accidental venue selection. Tests execute repeated requests, unsupported numeric inputs and invalid-mode reinitialization; all remain disconnected with no order ID or send. New code should implement the reviewed sidecar/IPC contract against the actual pinned QMT runtime rather than revive speculative callbacks. External users of the former experimental C++ callback API must migrate explicitly; no universal source compatibility is claimed. CTP remains an unselected experimental scaffold and similarly must not manufacture transport state. No current OMS/HSL reader or authority guard is removed by this development choice.
