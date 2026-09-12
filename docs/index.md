# HeptaTrader documentation index

Status: CURRENT
Applies to: repository HEAD
Owner: HeptaTrader maintainers
Verification: `python3 scripts/check_documentation.py` and `python3 scripts/check_component_coverage.py`

This index is the canonical entry point for current development documentation. A document is operational only when its status is `CURRENT` and its implementation and test paths are present in `docs/module-catalog.json`. Production-path completeness is independently derived from the exact Git index by `scripts/check_component_coverage.py`.

## Status vocabulary

- **CURRENT** — implemented in the canonical runtime and maintained.
- **QUALIFICATION_REQUIRED** — implemented, but real broker mutation remains fail-closed until external qualification succeeds.
- **EXPERIMENTAL** — source exists for research or interface stabilization; it must not advertise real venue authority.
- **LEGACY** — retained only for compatibility or migration and excluded from the default build.
- **PROPOSAL** — design material that must not be represented as installed or operational.
- **UNAVAILABLE** — intentionally absent; no caller may infer authority.

## Canonical module documents

| Module | Status | Document |
|---|---|---|
| Agent entry and MCP bridge | CURRENT | [`modules/agent-entry.md`](modules/agent-entry.md) |
| Tool Gateway and tool registry | CURRENT | [`modules/tool-gateway.md`](modules/tool-gateway.md) |
| Session supervisor | CURRENT | [`modules/session-supervisor.md`](modules/session-supervisor.md) |
| Execution Service | CURRENT | [`modules/execution-service.md`](modules/execution-service.md) |
| OMS journal and recovery | CURRENT | [`modules/oms-journal.md`](modules/oms-journal.md) |
| Risk engine | CURRENT | [`modules/risk-engine.md`](modules/risk-engine.md) |
| Authoritative state | CURRENT | [`modules/authoritative-state.md`](modules/authoritative-state.md) |
| Deterministic simulator | CURRENT | [`modules/simulator.md`](modules/simulator.md) |
| IB PAPER runtime | QUALIFICATION_REQUIRED | [`modules/ib-paper.md`](modules/ib-paper.md) |
| CTP adapter | EXPERIMENTAL | [`modules/ctp-adapter.md`](modules/ctp-adapter.md) |
| XT/QMT adapter | EXPERIMENTAL | [`modules/xt-adapter.md`](modules/xt-adapter.md) |
| SHADOW research pipeline | EXPERIMENTAL | [`modules/shadow-research.md`](modules/shadow-research.md) |
| Release engineering and host preflight | CURRENT | [`modules/release-engineering.md`](modules/release-engineering.md) |
| systemd deployment assets | CURRENT | [`modules/deployment.md`](modules/deployment.md) |
| Repository control and source verification | CURRENT | [`modules/repository-control.md`](modules/repository-control.md) |
| Historical monolith and compatibility assets | LEGACY | [`modules/legacy-runtime.md`](modules/legacy-runtime.md) |

## Development references

- [`DEVELOPMENT-DOCUMENTATION-INDEX.md`](DEVELOPMENT-DOCUMENTATION-INDEX.md)
- [`technical/runtime-engineering-map.md`](technical/runtime-engineering-map.md)
- [`technical/execution-events.md`](technical/execution-events.md)
- [`technical/reconciliation-engine.md`](technical/reconciliation-engine.md)
- [`technical/service-lifecycle.md`](technical/service-lifecycle.md)
- [`technical/build-supply-chain.md`](technical/build-supply-chain.md)
- [`technical/component-coverage.md`](technical/component-coverage.md)
- [`technical/release-simulator-smoke.md`](technical/release-simulator-smoke.md)
- [`technical/ib-paper-harness-contract.md`](technical/ib-paper-harness-contract.md)
- [`technical/ib-paper-host-driver.md`](technical/ib-paper-host-driver.md)
- [`technical/risk-legacy-compatibility.md`](technical/risk-legacy-compatibility.md)

## Operations

- [`operations/install.md`](operations/install.md)
- [`operations/release-package.md`](operations/release-package.md)
- [`operations/preflight.md`](operations/preflight.md)
- [`operations/paper-continuation.md`](operations/paper-continuation.md)
- [`operations/configure.md`](operations/configure.md)
- [`operations/startup-shutdown.md`](operations/startup-shutdown.md)
- [`operations/incident.md`](operations/incident.md)
- [`operations/rollback-backup.md`](operations/rollback-backup.md)

## Cross-cutting contracts

- [`AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md`](AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md)
- [`OMS-EVENT-SCHEMA.md`](OMS-EVENT-SCHEMA.md)
- [`RUNBOOK-KILLSWITCH.md`](RUNBOOK-KILLSWITCH.md)
- [`BROKER-NETWORK-ISOLATION.md`](BROKER-NETWORK-ISOLATION.md)
- [`DOCUMENTATION-POLICY.md`](DOCUMENTATION-POLICY.md)
- [`GAP-REGISTER.md`](GAP-REGISTER.md)
- [`RELEASE-PUBLICATION-SECURITY.md`](RELEASE-PUBLICATION-SECURITY.md)
- [`adr/0001-release-publication-atomicity.md`](adr/0001-release-publication-atomicity.md)
- [`adr/0002-owner-operated-repository.md`](adr/0002-owner-operated-repository.md)
- [`adr/0003-immutable-artifact-paper-qualification.md`](adr/0003-immutable-artifact-paper-qualification.md)
- [`ib-paper-qualification-scenarios-v1.json`](ib-paper-qualification-scenarios-v1.json)
- [`preflight-policy-v1.json`](preflight-policy-v1.json)
- [`gap-register.json`](gap-register.json)
- [`capabilities.json`](capabilities.json)
- [`module-catalog.json`](module-catalog.json)
- [`build-targets.json`](build-targets.json)

## Non-canonical material

The historical `HeptaStrategy/`, `HeptaSimulator/`, old Visual Studio projects, large legacy data files, and deprecated bridges are explicitly owned by the LEGACY module and excluded from the default build. Their presence does not authorize deployment or provide an alternate order path.

All active supported-scope source gaps, including canonical release/install/preflight engineering, Git-discovered component ownership, documentation depth, and Broker-free release rollback smoke, are closed in source. Optional IB PAPER activation remains separately qualification-gated and disabled by default; LIVE remains unavailable.
