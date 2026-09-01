# Hepta Contract Index

Status: generated current view
Applies to: all versioned inter-module contracts
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from contract-registry-v2.json

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

| Contract | Stability | Canonical document | Schema | Providers | Consumers |
|---|---|---|---|---|---|
| `capital-policy.v1` | target | [`contracts/GLOBAL-OPTIMIZATION-CONTRACT.md`](../contracts/GLOBAL-OPTIMIZATION-CONTRACT.md) | — | `hepta.management.control` | `hepta.global.decision`, `hepta.portfolio.compiler` |
| `client.mcp.v1` | current-core | [`contracts/CONTRACT-INDEX.md`](../contracts/CONTRACT-INDEX.md) | — | `hepta.client.runtime` | none |
| `client.native.v1` | current-core | [`contracts/CONTRACT-INDEX.md`](../contracts/CONTRACT-INDEX.md) | — | `hepta.client.runtime` | none |
| `decision-lease.v1` | current-core | [`contracts/IDENTITY-CAPABILITY-CONTRACT.md`](../contracts/IDENTITY-CAPABILITY-CONTRACT.md) | — | `hepta.agent.support` | `hepta.execution.runtime` |
| `execution.client.v1` | current-core | [`contracts/EXECUTION-AUTHORITY-CONTRACT.md`](../contracts/EXECUTION-AUTHORITY-CONTRACT.md) | — | `hepta.execution.runtime` | `hepta.gateway.runtime` |
| `execution.permit.v1` | current-core | [`contracts/TARGET-POSITION-INTENT-CONTRACT.md`](../contracts/TARGET-POSITION-INTENT-CONTRACT.md) | — | `hepta.execution.runtime` | `hepta.execution.runtime` |
| `feature-snapshot.v1` | target | [`research/FEATURE-REGISTRY.md`](../research/FEATURE-REGISTRY.md) | — | `hepta.feature.runtime` | `hepta.strategy.runtime` |
| `gateway.session-boundary` | current-core | [`contracts/IDENTITY-CAPABILITY-CONTRACT.md`](../contracts/IDENTITY-CAPABILITY-CONTRACT.md) | — | `hepta.gateway.runtime` | none |
| `gateway.tool-dispatch` | current-core | [`contracts/IDENTITY-CAPABILITY-CONTRACT.md`](../contracts/IDENTITY-CAPABILITY-CONTRACT.md) | — | `hepta.gateway.runtime` | none |
| `hepta.allocation-plan.v1` | target | [`contracts/ALLOCATION-PLAN-CONTRACT.md`](../contracts/ALLOCATION-PLAN-CONTRACT.md) | `../../schemas/allocation-plan-v1.json` | `hepta.global.decision` | `hepta.execution.runtime`, `hepta.portfolio.compiler`, `hepta.simulation.runtime` |
| `hepta.authoritative-snapshot.v2` | current-core | [`contracts/AUTHORITATIVE-SNAPSHOT-CONTRACT.md`](../contracts/AUTHORITATIVE-SNAPSHOT-CONTRACT.md) | `../../schemas/authoritative-snapshot-v2.json` | `hepta.execution.runtime` | `hepta.execution.runtime`, `hepta.global.decision`, `hepta.risk.policy`, `hepta.simulation.runtime` |
| `hepta.configuration-authority.v1` | target | [`contracts/CONFIGURATION-AUTHORITY-CONTRACT.md`](../contracts/CONFIGURATION-AUTHORITY-CONTRACT.md) | — | `hepta.management.control` | `hepta.execution.runtime`, `hepta.gateway.runtime` |
| `hepta.event-envelope.v1` | target | [`contracts/EVENT-ORDERING-CONTRACT.md`](../contracts/EVENT-ORDERING-CONTRACT.md) | `../../schemas/event-envelope-v1.json` | `hepta.protocol.contracts` | `hepta.execution.runtime`, `hepta.venue.ib`, `hepta.venue.simulator` |
| `hepta.execution-authority.v1` | current-core | [`contracts/EXECUTION-AUTHORITY-CONTRACT.md`](../contracts/EXECUTION-AUTHORITY-CONTRACT.md) | `../../schemas/execution-wire-v1.json` | `hepta.execution.runtime` | `hepta.execution.runtime`, `hepta.gateway.runtime` |
| `hepta.execution-wire.v1` | current-core | [`contracts/CONTRACT-INDEX.md`](../contracts/CONTRACT-INDEX.md) | `../../schemas/execution-wire-v1.json` | `hepta.protocol.contracts` | `hepta.execution.runtime`, `hepta.gateway.runtime` |
| `hepta.global-optimization.v1` | target | [`contracts/GLOBAL-OPTIMIZATION-CONTRACT.md`](../contracts/GLOBAL-OPTIMIZATION-CONTRACT.md) | — | `hepta.global.decision` | `hepta.simulation.runtime` |
| `hepta.ib-paper-qualification.v1` | target-external | [`operations/IB-PAPER-QUALIFICATION.md`](../operations/IB-PAPER-QUALIFICATION.md) | — | external/none | none |
| `hepta.identity-capability.v1` | current-core | [`contracts/IDENTITY-CAPABILITY-CONTRACT.md`](../contracts/IDENTITY-CAPABILITY-CONTRACT.md) | — | `hepta.gateway.runtime` | `hepta.gateway.runtime` |
| `hepta.metric-registry.v1` | target | [`operations/OBSERVABILITY.md`](../operations/OBSERVABILITY.md) | `../../docs/verification/metric-registry-v1.json` | `hepta.documentation.control` | `hepta.observability.runtime` |
| `hepta.module-lifecycle.v1` | target | [`contracts/MODULE-LIFECYCLE-CONTRACT.md`](../contracts/MODULE-LIFECYCLE-CONTRACT.md) | `../../schemas/module-lifecycle-v1.json` | `hepta.management.control` | `hepta.global.decision`, `hepta.simulation.runtime`, `hepta.strategy.runtime` |
| `hepta.module-manifest.v2` | target | [`modules/MODULE-MANIFEST-SPEC.md`](../modules/MODULE-MANIFEST-SPEC.md) | `../../docs/modules/module-manifest-schema-v2.json` | `hepta.documentation.control` | `hepta.management.control` |
| `hepta.numeric.fixed-v1` | target | [`architecture/NUMERIC-POLICY.md`](../architecture/NUMERIC-POLICY.md) | — | `hepta.numeric.core` | `hepta.execution.runtime`, `hepta.global.decision`, `hepta.portfolio.compiler`, `hepta.risk.policy`, `hepta.simulation.runtime` |
| `hepta.oms-journal.v3` | current-core | [`contracts/OMS-JOURNAL-CONTRACT.md`](../contracts/OMS-JOURNAL-CONTRACT.md) | — | `hepta.execution.runtime` | `hepta.execution.runtime` |
| `hepta.reason-code.v1` | target | [`contracts/REASON-CODE-CONTRACT.md`](../contracts/REASON-CODE-CONTRACT.md) | `../../docs/verification/reason-code-registry-v1.json` | `hepta.documentation.control` | `hepta.execution.runtime`, `hepta.observability.runtime`, `hepta.risk.policy` |
| `hepta.research-run.v1` | current-core | [`research/RESEARCH-PROTOCOL.md`](../research/RESEARCH-PROTOCOL.md) | `../../schemas/research-run-v1.json` | `hepta.research.protocol` | none |
| `hepta.risk-policy.v2` | current-core | [`contracts/RISK-POLICY-CONTRACT.md`](../contracts/RISK-POLICY-CONTRACT.md) | — | `hepta.risk.policy` | `hepta.execution.runtime` |
| `hepta.solver-result.v1` | target | [`contracts/SOLVER-RESULT-CONTRACT.md`](../contracts/SOLVER-RESULT-CONTRACT.md) | `../../schemas/solver-result-v1.json` | `hepta.global.decision` | `hepta.execution.runtime`, `hepta.simulation.runtime` |
| `hepta.strategy-proposal.v1` | target | [`contracts/STRATEGY-PROPOSAL-CONTRACT.md`](../contracts/STRATEGY-PROPOSAL-CONTRACT.md) | `../../schemas/strategy-proposal-v1.json` | `hepta.strategy.runtime` | `hepta.global.decision`, `hepta.portfolio.compiler`, `hepta.simulation.runtime` |
| `hepta.target-position-intent.v1` | current-core | [`contracts/TARGET-POSITION-INTENT-CONTRACT.md`](../contracts/TARGET-POSITION-INTENT-CONTRACT.md) | `../../schemas/target-position-intent-v1.json` | `hepta.execution.runtime` | `hepta.execution.runtime` |
| `hepta.tool-catalog.v1` | current-core | [`contracts/CONTRACT-INDEX.md`](../contracts/CONTRACT-INDEX.md) | `../../schemas/tool-catalog-v1.json` | `hepta.gateway.runtime` | `hepta.client.runtime` |
| `hepta.venue.v1` | current-core | [`contracts/EXECUTION-AUTHORITY-CONTRACT.md`](../contracts/EXECUTION-AUTHORITY-CONTRACT.md) | — | `hepta.venue.ctp`, `hepta.venue.ib`, `hepta.venue.simulator`, `hepta.venue.xt` | `hepta.execution.runtime` |
| `market-event.v1` | target | [`research/POINT-IN-TIME-DATA-CONTRACT.md`](../research/POINT-IN-TIME-DATA-CONTRACT.md) | — | `hepta.marketdata.runtime` | `hepta.feature.runtime` |
| `portfolio.net-target.v1` | current-core | [`contracts/ALLOCATION-PLAN-CONTRACT.md`](../contracts/ALLOCATION-PLAN-CONTRACT.md) | — | `hepta.portfolio.compiler` | `hepta.execution.runtime`, `hepta.risk.policy`, `hepta.simulation.runtime` |
| `proposal-set.v1` | target | [`contracts/STRATEGY-PROPOSAL-CONTRACT.md`](../contracts/STRATEGY-PROPOSAL-CONTRACT.md) | — | `hepta.global.decision` | `hepta.global.decision`, `hepta.simulation.runtime` |
| `reconcile.decision.v1` | current-core | [`operations/RECONCILIATION.md`](../operations/RECONCILIATION.md) | — | `hepta.execution.runtime` | `hepta.execution.runtime` |
| `telemetry.runtime.v1` | current-core | [`operations/OBSERVABILITY.md`](../operations/OBSERVABILITY.md) | — | `hepta.observability.runtime` | none |
