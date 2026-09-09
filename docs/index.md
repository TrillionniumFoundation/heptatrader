# HeptaTrader documentation index

Status: CURRENT
Applies to: repository HEAD
Owner: HeptaTrader maintainers
Verification: `python3 scripts/check_documentation.py`

This index is the canonical entry point for current development documentation. A document is operational only when its status is `CURRENT` and its implementation and test paths are present in `docs/module-catalog.json`.

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

## Operations

- [`operations/install.md`](operations/install.md)
- [`operations/release-package.md`](operations/release-package.md)
- [`operations/preflight.md`](operations/preflight.md)
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
- [`preflight-policy-v1.json`](preflight-policy-v1.json)
- [`gap-register.json`](gap-register.json)
- [`capabilities.json`](capabilities.json)
- [`module-catalog.json`](module-catalog.json)

## Non-canonical material

The top-level `HeptaStrategy/`, `HeptaSimulator/`, old Visual Studio projects, old PowerShell-oriented notes, and legacy reconciliation examples are not part of the canonical runtime unless explicitly named by the module catalog. Historical documents must be labeled `LEGACY` or `PROPOSAL`; their existence does not authorize deployment.
- [`adr/0002-owner-operated-repository.md`](adr/0002-owner-operated-repository.md)

All active supported-scope gaps, including canonical release/install/preflight engineering, are closed in source. Optional IB PAPER activation remains separately qualification-gated and disabled by default; LIVE remains unavailable.
