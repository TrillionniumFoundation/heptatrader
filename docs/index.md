# HeptaTrader documentation index

Status: CURRENT
Applies to: repository HEAD
Owner: HeptaTrader maintainers
Verification: `python3 scripts/check_documentation.py` and `python3 scripts/check_component_coverage.py`

This is the canonical development entry point. The module catalog maps maintained
implementation and test paths; Git/CMake checks detect missing ownership and
structural drift. Neither a CURRENT label nor a green source check proves design
completeness, host readiness or permission to trade. Technical contracts below
state their implemented behavior, evidence and remaining limits separately.

## Status vocabulary

- **CURRENT** — maintained implementation or current contract; its stated scope still applies.
- **QUALIFICATION_REQUIRED** — implemented candidate; real Broker mutation remains disabled until external qualification.
- **EXPERIMENTAL** — research or interface scaffold, not real venue authority.
- **LEGACY** — compatibility/provenance only, not a supported alternate trading runtime.
- **PROPOSAL** — design not represented as installed or operational.
- **UNAVAILABLE** — intentionally absent; authority cannot be inferred.

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

## Runtime and protocol references

| Area | Development references |
|---|---|
| Architecture and change impact | [runtime map](technical/runtime-engineering-map.md), [architecture](AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md), [historical entry redirect](DEVELOPMENT-DOCUMENTATION-INDEX.md) |
| Agent and native wire | [Agent tools](technical/agent-tool-protocol.md), [operation contracts](technical/wire-operation-contracts.md), [generated field reference](technical/wire-field-reference.md), [capability advertising](technical/capability-advertising.md) |
| Execution and risk | [venue placement](technical/venue-placement-contract.md), [cancellation](technical/venue-cancellation-contract.md), [execution events](technical/execution-events.md), [reconciliation](technical/reconciliation-engine.md), [retired risk compatibility](technical/risk-legacy-compatibility.md) |
| Durable recovery | [OMS event schema](OMS-EVENT-SCHEMA.md), [lease format](technical/session-lease-format.md), [recovery capacity](technical/oms-recovery-capacity.md), [coordinator recovery memory](technical/coordinator-recovery-memory.md), [persistence support](technical/persistence-support-window.md), [lossless archive](technical/oms-archive-lifecycle.md) |
| Lifecycle and development venue | [service lifecycle](technical/service-lifecycle.md), [simulator walkthrough](technical/simulator-operator-walkthrough.md), [systemd acceptance](technical/systemd-simulator-acceptance.md) |
| Runtime observations | [metric inventory](OBSERVABILITY-METRICS.md), [cost boundaries](technical/runtime-cost-observations.md), [live OMS capacity](technical/oms-live-capacity.md), [pending queue](technical/oms-pending-queue.md), [OMS report](technical/oms-operational-report.md), [Gateway metrics](technical/gateway-runtime-observability.md), [collection](technical/telemetry-collection.md) |
| Research | [SHADOW pipeline contract](technical/shadow-pipeline-contract.md) |

## Operations and release

| Area | References |
|---|---|
| Install and configure | [installation](operations/install.md), [configuration](operations/configure.md), [startup/shutdown](operations/startup-shutdown.md) |
| Release and recovery | [release packaging](operations/release-package.md), [preflight](operations/preflight.md), [rollback/backup](operations/rollback-backup.md), [simulator smoke](technical/release-simulator-smoke.md), [core acceptance](technical/core-release-acceptance.md) |
| Incident response | [operations incident](operations/incident.md), [incident runbook](RUNBOOK-INCIDENT.md), [kill switch](RUNBOOK-KILLSWITCH.md), [alert rules](ALERT-RULES-BASELINE.md) |
| Supply chain and isolation | [build supply chain](technical/build-supply-chain.md), [publication security](RELEASE-PUBLICATION-SECURITY.md), [Broker network isolation](BROKER-NETWORK-ISOLATION.md), [tagged release workflow](../.github/workflows/release.yml) |
| Optional IB qualification | [harness contract](technical/ib-paper-harness-contract.md), [scenario inventory](ib-paper-qualification-scenarios-v1.json) |

## Source ownership and decisions

[Documentation policy](DOCUMENTATION-POLICY.md), [component coverage](technical/component-coverage.md),
[gap-register guide](GAP-REGISTER.md), [iteration status](ITERATION.md) and
[owner Ruleset transition](technical/owner-ruleset-transition.md) describe separate
source/development concerns. They do not change hosted permissions or authorize
Broker actions.

The machine-readable sources are [module catalog](module-catalog.json),
[capabilities](capabilities.json), [build targets](build-targets.json),
[gap register](gap-register.json) and [preflight policy](preflight-policy-v1.json).
The decisions are [publication atomicity](adr/0001-release-publication-atomicity.md),
[owner operation](adr/0002-owner-operated-repository.md) and
[immutable artifact qualification](adr/0003-immutable-artifact-paper-qualification.md).

## Supporting research and historical references

Experimental/proposal material remains explicitly scoped:
[EUR/USD SHADOW v2](EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md),
[IB latency research](IB-SYSTEM-LOW-LATENCY.md), [QMT SDK review](QMT-SDK-REVIEW.md),
[strategy validation](STRATEGY-VALIDATION-PLAN.md), [XT mapping](XT-HEPTA-MAPPING.md)
and [XT venue plan](XTQMT-VENUE-PLAN.md).

Legacy notes preserve context, not active runtime specifications:
[profile lock](CONFIG-PROFILE-LOCK.md), [QMT bridge](QMT-BRIDGE-MVP.md),
[CSV reconciliation](RECONCILE-RULES.md), [old market-data format](SIM-MD-FORMAT.md)
and [old strategy persistence](strategy-state-persist-min.md).
The 2023 [system introduction PDF](../doc/HeptaTrader系统介绍.pdf) and root
`SECURITY-HARDENING.md` are historical material, not current build, release or
trading-authority evidence.

## Retired source and open scope

The historical monolith, HeptaStrategy, Pegasus, dedicated CSV reporter, bridges
and old XML build graph were retired. Root `Interface/` and `Tools/`, including
the old vendor/header copy and dated samples, and the personal-path
`validate_sim_data.py` helper are also removed from the active source/package.
The maintained `HeptaTrade/tools/` code and all supported journal/lease readers
are unchanged. [Retirement](technical/legacy-retirement.md) records consumers,
provenance and limits. Legacy CMake ON requests fail explicitly; OFF is accepted.

Open work is recorded without forcing artificial closure. Permanent-history
capacity, target-host multi-day acceptance and hosted Ruleset changes remain
separate from repository tests. IB PAPER is qualification-gated and disabled by
default; LIVE remains unavailable.
