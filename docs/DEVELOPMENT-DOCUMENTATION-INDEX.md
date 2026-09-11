# HeptaTrader development documentation index

Status: CURRENT  
Applies to: repository HEAD  
Verification: `python3 scripts/check_component_coverage.py`

This index connects every registered module document with the deeper implementation references used for development, review, operations, and incident response.

## Module contracts

- [`docs/modules/agent-entry.md`](modules/agent-entry.md)
- [`docs/modules/tool-gateway.md`](modules/tool-gateway.md)
- [`docs/modules/session-supervisor.md`](modules/session-supervisor.md)
- [`docs/modules/execution-service.md`](modules/execution-service.md)
- [`docs/modules/oms-journal.md`](modules/oms-journal.md)
- [`docs/modules/risk-engine.md`](modules/risk-engine.md)
- [`docs/modules/authoritative-state.md`](modules/authoritative-state.md)
- [`docs/modules/simulator.md`](modules/simulator.md)
- [`docs/modules/ib-paper.md`](modules/ib-paper.md)
- [`docs/modules/ctp-adapter.md`](modules/ctp-adapter.md)
- [`docs/modules/xt-adapter.md`](modules/xt-adapter.md)
- [`docs/modules/shadow-research.md`](modules/shadow-research.md)
- [`docs/modules/release-engineering.md`](modules/release-engineering.md)
- [`docs/modules/deployment.md`](modules/deployment.md)
- [`docs/modules/repository-control.md`](modules/repository-control.md)
- [`docs/modules/legacy-runtime.md`](modules/legacy-runtime.md)

## Detailed implementation references

- [`docs/technical/execution-events.md`](technical/execution-events.md)
- [`docs/technical/reconciliation-engine.md`](technical/reconciliation-engine.md)
- [`docs/technical/service-lifecycle.md`](technical/service-lifecycle.md)
- [`docs/technical/build-supply-chain.md`](technical/build-supply-chain.md)
- [`docs/technical/component-coverage.md`](technical/component-coverage.md)
- [`docs/technical/release-simulator-smoke.md`](technical/release-simulator-smoke.md)

## Required depth

A maintained subsystem document should state ownership, public contracts, configuration/state, concurrency where applicable, persistence and migration, failure semantics, security boundary, observability, executable tests, operational procedure, and known limitations. Source status and trading authorization are separate: simulator is current, IB PAPER requires external qualification, CTP/XT have no real transport, and LIVE is unavailable.
