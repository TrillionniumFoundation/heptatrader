# CTP adapter

Status: EXPERIMENTAL  
Applies to: repository HEAD  
Implementation: `HeptaTrade/adapter_ctp/`  
Tests: `tests/venue_capability_tests.cpp`

## Current capability

The CTP adapter is an interface scaffold only. It does not implement a real CTP transport, authentication, settlement confirmation, order insertion, cancel, query, callback normalization, recovery, or reconciliation path.

`Connect()` must fail closed with an explicit unsupported reason. The module must not report a connected state and must not be advertised as a real venue in discovery, deployment, or architecture diagrams.

## Intended responsibilities

A future implementation may translate CTP-specific request and callback semantics into the common Execution venue contract. It must not own strategy or portfolio policy and must not bypass the Execution Service.

Required future work includes:

- pinned, separately supplied CTP SDK and licensing boundary;
- front discovery and secure configuration;
- authentication/login and settlement confirmation state machine;
- order ref, front/session ID, exchange ID, order sys ID, trade ID correlation;
- order, trade, position, account, instrument, and market-data callbacks;
- reconnect and query barriers;
- SHFE/INE close-today/close-yesterday semantics;
- price tick, volume multiple, trading-day, and exchange-state checks;
- durable uncertain-order recovery;
- venue-specific qualification fixtures.

## Security boundary

A future CTP process must run under a dedicated OS identity with dedicated credentials and egress. Agent and Gateway processes must not load the vendor library, read credentials, or reach the CTP front directly.

## Failure semantics

Until the transport and qualification are implemented, every real connection or mutation request is rejected as unsupported. Fake acknowledgements, local order IDs, or synthetic connected events are forbidden outside the deterministic simulator.

## Observability and tests

The current test asserts that the scaffold identifies itself as experimental and cannot connect or submit. A future adapter requires state-machine, callback, correlation, partial-fill, disconnect, query-barrier, trading-day rollover, and fault-injection tests before its status can change.
