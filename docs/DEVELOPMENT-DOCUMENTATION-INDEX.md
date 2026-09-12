# HeptaTrader development documentation index

Status: CURRENT  
Applies to: repository HEAD  
Verification: `python3 scripts/check_documentation.py` and `python3 scripts/check_component_coverage.py`

This index connects every registered module document with the deeper implementation references used for development, review, operations, qualification, and incident response.

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

- [`docs/technical/runtime-engineering-map.md`](technical/runtime-engineering-map.md)
- [`docs/technical/execution-events.md`](technical/execution-events.md)
- [`docs/technical/reconciliation-engine.md`](technical/reconciliation-engine.md)
- [`docs/technical/service-lifecycle.md`](technical/service-lifecycle.md)
- [`docs/technical/build-supply-chain.md`](technical/build-supply-chain.md)
- [`docs/technical/component-coverage.md`](technical/component-coverage.md)
- [`docs/technical/release-simulator-smoke.md`](technical/release-simulator-smoke.md)
- [`docs/technical/ib-paper-harness-contract.md`](technical/ib-paper-harness-contract.md)
- [`docs/technical/risk-legacy-compatibility.md`](technical/risk-legacy-compatibility.md)
- [`docs/technical/capability-advertising.md`](technical/capability-advertising.md)
- [`docs/ib-paper-qualification-scenarios-v1.json`](ib-paper-qualification-scenarios-v1.json)
- [`docs/adr/0003-immutable-artifact-paper-qualification.md`](adr/0003-immutable-artifact-paper-qualification.md)

## Required depth

A maintained subsystem document must contain substantive engineering prose rather than only metadata and path inventories. `scripts/check_documentation.py` requires maintained modules to cover a broad set of purpose/contracts/state/concurrency/failure/security/observability/testing/operations/limitations topics while allowing module-specific wording. Experimental and legacy material has a lower threshold but must still explain capability, failure and test boundaries.

Compatibility fields that remain for historical source callers are not exempt from authority boundaries. `tests/python/test_risk_legacy_compatibility_boundary.py` rejects any canonical C++ implementation that consumes the old unbound risk scalars; production code must use the authoritative snapshot and converted-notional evidence contracts.

Source status and trading authorization are separate: simulator is current, IB PAPER requires external broker-observed qualification, CTP/XT have no real transport, and LIVE is unavailable. A green documentation or repository-control check never implies Broker authorization.

The PAPER qualification subject is the exact dispatch-time source plus the immutable candidate artifact and its bound runtime evidence. Later movement of `main` is not an artifact mutation and is deliberately not a qualification invalidator; see ADR 0003.

## Interface and installed-process development references

- [Agent tool protocol](technical/agent-tool-protocol.md)
- [SHADOW data contracts](technical/shadow-data-contracts.md)
- [Installed process acceptance](technical/installed-process-acceptance.md)
