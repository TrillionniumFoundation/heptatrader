# HeptaTrader documentation index

Status: CURRENT
Applies to: repository HEAD
Owner: HeptaTrader maintainers
Verification: `python3 scripts/check_documentation.py` and `python3 scripts/check_component_coverage.py`

This index is the canonical entry point for current development documentation. A document is operational only when its status is `CURRENT` and its implementation and test paths are present in `docs/module-catalog.json`. Component ownership is independently derived from the Git index by `scripts/check_component_coverage.py`; ownership and structural lint are not proof of complete technical design or behavior.

## Status vocabulary

- **CURRENT** — implemented in the canonical runtime and maintained.
- **QUALIFICATION_REQUIRED** — implemented, but real broker mutation remains fail-closed until external qualification succeeds.
- **EXPERIMENTAL** — source exists for research or interface stabilization; it must not advertise real venue authority.
- **LEGACY** — retained only for compatibility or migration and excluded from the default build.
- **PROPOSAL** — design material that must not be represented as installed or operational.
- **UNAVAILABLE** — intentionally absent; no caller may infer authority.

## Canonical module documents

<!-- module-catalog:begin -->
| Module | Status | Development document |
|---|---|---|
| `agent-entry` | CURRENT | [modules/agent-entry.md](modules/agent-entry.md) |
| `tool-gateway` | CURRENT | [modules/tool-gateway.md](modules/tool-gateway.md) |
| `session-supervisor` | CURRENT | [modules/session-supervisor.md](modules/session-supervisor.md) |
| `execution-service` | CURRENT | [modules/execution-service.md](modules/execution-service.md) |
| `oms-journal` | CURRENT | [modules/oms-journal.md](modules/oms-journal.md) |
| `risk-engine` | CURRENT | [modules/risk-engine.md](modules/risk-engine.md) |
| `authoritative-state` | CURRENT | [modules/authoritative-state.md](modules/authoritative-state.md) |
| `simulator` | CURRENT | [modules/simulator.md](modules/simulator.md) |
| `ib-paper` | QUALIFICATION_REQUIRED | [modules/ib-paper.md](modules/ib-paper.md) |
| `ctp-adapter` | EXPERIMENTAL | [modules/ctp-adapter.md](modules/ctp-adapter.md) |
| `xt-adapter` | EXPERIMENTAL | [modules/xt-adapter.md](modules/xt-adapter.md) |
| `shadow-research` | EXPERIMENTAL | [modules/shadow-research.md](modules/shadow-research.md) |
| `release-engineering` | CURRENT | [modules/release-engineering.md](modules/release-engineering.md) |
| `deployment` | CURRENT | [modules/deployment.md](modules/deployment.md) |
| `repository-control` | CURRENT | [modules/repository-control.md](modules/repository-control.md) |
| `legacy-runtime` | LEGACY | [modules/legacy-runtime.md](modules/legacy-runtime.md) |
<!-- module-catalog:end -->

## Development references

- [`DEVELOPMENT-DOCUMENTATION-INDEX.md`](DEVELOPMENT-DOCUMENTATION-INDEX.md)
- [`technical/runtime-engineering-map.md`](technical/runtime-engineering-map.md)
- [`technical/agent-tool-protocol.md`](technical/agent-tool-protocol.md)
- [`technical/session-lease-format.md`](technical/session-lease-format.md)
- [`technical/shadow-pipeline-contract.md`](technical/shadow-pipeline-contract.md)
- [`technical/legacy-retirement.md`](technical/legacy-retirement.md)
- [`technical/execution-events.md`](technical/execution-events.md)
- [`technical/reconciliation-engine.md`](technical/reconciliation-engine.md)
- [`technical/service-lifecycle.md`](technical/service-lifecycle.md)
- [`technical/build-supply-chain.md`](technical/build-supply-chain.md)
- [`technical/component-coverage.md`](technical/component-coverage.md)
- [`technical/release-simulator-smoke.md`](technical/release-simulator-smoke.md)
- [`technical/ib-paper-harness-contract.md`](technical/ib-paper-harness-contract.md)
- [`technical/risk-legacy-compatibility.md`](technical/risk-legacy-compatibility.md)
- [`technical/capability-advertising.md`](technical/capability-advertising.md)

- [`technical/systemd-simulator-acceptance.md`](technical/systemd-simulator-acceptance.md)
- [`technical/simulator-operator-walkthrough.md`](technical/simulator-operator-walkthrough.md)

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
- Tagged release workflow: [`.github/workflows/release.yml`](../.github/workflows/release.yml)
- [`adr/0001-release-publication-atomicity.md`](adr/0001-release-publication-atomicity.md)
- [`adr/0002-owner-operated-repository.md`](adr/0002-owner-operated-repository.md)
- [`adr/0003-immutable-artifact-paper-qualification.md`](adr/0003-immutable-artifact-paper-qualification.md)
- [`ib-paper-qualification-scenarios-v1.json`](ib-paper-qualification-scenarios-v1.json)
- [`preflight-policy-v1.json`](preflight-policy-v1.json)
- [`gap-register.json`](gap-register.json)
- [`capabilities.json`](capabilities.json)
- [`module-catalog.json`](module-catalog.json)
- [`build-targets.json`](build-targets.json)

## Supporting and historical references

These documents are intentionally outside the module catalog because they are
cross-cutting runbooks, research notes, or compatibility material. Their
status line is authoritative: `CURRENT` documents describe maintained
contracts, while `EXPERIMENTAL`, `PROPOSAL`, and `LEGACY` documents cannot
authorize a venue or deployment path.

### Current cross-cutting contracts

- [`ALERT-RULES-BASELINE.md`](ALERT-RULES-BASELINE.md)
- [`ITERATION.md`](ITERATION.md)
- [`OBSERVABILITY-METRICS.md`](OBSERVABILITY-METRICS.md)
- [`RUNBOOK-INCIDENT.md`](RUNBOOK-INCIDENT.md)

### Experimental and proposal material

- [`EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md`](EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md)
- [`IB-SYSTEM-LOW-LATENCY.md`](IB-SYSTEM-LOW-LATENCY.md)
- [`QMT-SDK-REVIEW.md`](QMT-SDK-REVIEW.md)
- [`STRATEGY-VALIDATION-PLAN.md`](STRATEGY-VALIDATION-PLAN.md)
- [`XT-HEPTA-MAPPING.md`](XT-HEPTA-MAPPING.md)
- [`XTQMT-VENUE-PLAN.md`](XTQMT-VENUE-PLAN.md)

### Legacy compatibility notes

- [`CONFIG-PROFILE-LOCK.md`](CONFIG-PROFILE-LOCK.md)
- [`QMT-BRIDGE-MVP.md`](QMT-BRIDGE-MVP.md)
- [`RECONCILE-RULES.md`](RECONCILE-RULES.md)
- [`SIM-MD-FORMAT.md`](SIM-MD-FORMAT.md)
- [`strategy-state-persist-min.md`](strategy-state-persist-min.md)

The 2023 [`doc/HeptaTrader系统介绍.pdf`](../doc/HeptaTrader系统介绍.pdf) is
archived historical material (its title and Windows monolith architecture do
not describe the current Agent-native Linux runtime). It is retained for
provenance only and is excluded from build, release, and authorization
evidence. `SECURITY-HARDENING.md` at the repository root is likewise a legacy
security note; current security contracts live in
[`BROKER-NETWORK-ISOLATION.md`](BROKER-NETWORK-ISOLATION.md) and the deployment
module document.

## Non-canonical material

The historical `HeptaStrategy/`, `HeptaSimulator/`, legacy CSV reconciliation reporter, large data files and deprecated bridges remain explicitly owned by the LEGACY module. Eight unused Visual Studio assets were retired; shared `Interface/` and `Tools/` headers remain where required. See [`technical/legacy-retirement.md`](technical/legacy-retirement.md). Legacy presence does not authorize deployment or provide an alternate order path.

Open work and release-profile blockers are recorded in `gap-register.json`. A green source check validates structure and ownership, not universal project completeness. Optional IB PAPER activation remains separately qualification-gated and disabled by default; LIVE remains unavailable.
