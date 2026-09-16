# CTP adapter

Status: EXPERIMENTAL  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_ctp`, `third_party/ctp`
Tests: `tests/venue_capability_tests.cpp`, `tests/python/test_legacy_runtime_boundary.py`

## Current capability

The CTP adapter is an interface scaffold only. It does not implement a real CTP transport, authentication, settlement confirmation, order insertion, cancel, query, callback normalization, recovery, or reconciliation path.

`Connect()` must fail closed with an explicit unsupported reason. The module must not report a connected state and must not be advertised as a real venue in discovery, deployment, or architecture diagrams.

## Selected implementation direction

CTP is the selected next experimental venue rather than XT/QMT because the repository already records one byte-exact CTP 6.7.7 header-overlay boundary while XT has no pinned SDK/runtime input. This selection does **not** change capability status or authorization.

The full implementation contract is [`../technical/ctp-adapter-contract.md`](../technical/ctp-adapter-contract.md). It specifies the SDK/build boundary, connection/auth/login/settlement state machine, request and order correlations, query barriers, close-today/close-yesterday semantics, durable uncertain-send recovery, failure matrix, observability and exact acceptance cases. Future CTP work must implement that contract rather than add disconnected callbacks or broker-shaped placeholders.

The active vendor boundary under `third_party/ctp/6.7.7` describes an operator-supplied overlay only. Historical `Interface/CTPTradeApi*` payloads were retired with the legacy runtime and are not a supported fallback SDK path.

## Intended responsibilities

A future implementation may translate CTP-specific request and callback semantics into the common Execution venue contract. It must not own strategy or portfolio policy and must not bypass the Execution Service.

The adapter must eventually provide:

- pinned, separately supplied CTP SDK and licensing boundary;
- front discovery and secure configuration;
- authentication/login and settlement confirmation state machine;
- service-owned order ref plus front/session, exchange order-system and trade-ID correlation;
- order, trade, position, account, instrument, and market-data callbacks;
- reconnect and generation-bounded query barriers;
- SHFE/INE close-today/close-yesterday semantics without implicit multi-send splitting;
- price tick, volume multiple, trading-day, and exchange-state checks;
- durable uncertain-order recovery through the canonical OMS/Execution authority;
- deterministic fault fixtures followed by separately authorized venue qualification.

## Security boundary

A future CTP process must run under a dedicated OS identity with dedicated credentials and egress. Agent and Gateway processes must not load the vendor library, read credentials, or reach the CTP front directly.

## Failure semantics

Until the transport and qualification are implemented, every real connection or mutation request is rejected as unsupported. Fake acknowledgements, local order IDs, or synthetic connected events are forbidden outside the deterministic simulator.

## Observability and tests

The current test asserts that the scaffold identifies itself as experimental and cannot connect or submit. Status promotion requires the executable matrix in the CTP implementation contract, including state-machine, callback, correlation, partial-fill, cancel/fill race, disconnect at durable boundaries, query-barrier, trading-day rollover and restart/no-resend tests. Matching an external SDK overlay alone is not transport or qualification evidence.
