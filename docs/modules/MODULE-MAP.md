# Hepta Module Map

Status: generated current view
Applies to: current and target module boundaries
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from module-registry-v2.json

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

| Module | Lifecycle | Authority | Trust domain | Build targets | Ownership | DRI / backup |
|---|---|---|---|---|---|---|
| `hepta.agent.support` | current | decision lease client and event hub | `trusted-local` | `hepta_agent_execution_support` | exclusive | @hepta/agent-runtime / @hepta/execution-core |
| `hepta.client.runtime` | current | CLI/native/MCP request encoding | `unprivileged-client` | `hepta_native_tool_client`, `heptactl`, `hepta_sessionctl` | exclusive | @hepta/sdk / @hepta/gateway |
| `hepta.documentation.control` | current | registries/generated views/module and build graph validators | `development` | — | exclusive | @hepta/documentation / @hepta/architecture |
| `hepta.execution.runtime` | current | only venue mutation/state/OMS/permit authority | `execution-authority` | `hepta_execution_client`, `hepta_execution_server`, `hepta_execution_core`, `hepta_executiond`, `hepta_ib_executiond`, `hepta_paper_terminal_latch_committer`, `hepta_oms_core`, `hepta_state_core`, `hepta_intent_core` | exclusive | @hepta/execution-core / @hepta/execution-safety |
| `hepta.feature.runtime` | planned | deterministic feature generations | `feature` | `hepta_feature_runtime` | exclusive | @hepta/data-platform / @hepta/research-validation |
| `hepta.gateway.runtime` | current | identity/session/capability/tool dispatch | `agent-gateway` | `hepta_agent_os_core`, `hepta_tool_gatewayd`, `hepta_trading_tool_core`, `hepta_execution_event_relay_core` | exclusive | @hepta/gateway / @hepta/security-runtime |
| `hepta.global.decision` | planned | proposal aggregation/global allocation | `global-decision` | `hepta_proposal_aggregator`, `hepta_global_allocator` | exclusive | @hepta/global-allocation / @hepta/risk |
| `hepta.management.control` | planned | module/config/resource lifecycle | `management` | `hepta_managementd` | exclusive | @hepta/platform / @hepta/architecture |
| `hepta.marketdata.runtime` | planned | normalized point-in-time events | `market-data` | `hepta_marketdata_core` | exclusive | @hepta/data-platform / @hepta/venue-ib |
| `hepta.numeric.core` | planned | fixed numeric boundary | `shared-trusted` | `hepta_numeric_core` | exclusive | @hepta/contracts / @hepta/risk |
| `hepta.observability.runtime` | current | bounded telemetry | `shared-trusted` | `hepta_observability_core` | exclusive | @hepta/reliability / @hepta/platform |
| `hepta.portfolio.compiler` | current | deterministic netting/budget | `portfolio-risk` | `hepta_portfolio_core` | exclusive | @hepta/portfolio / @hepta/risk |
| `hepta.protocol.contracts` | current | wire/codec | `shared-trusted` | `hepta_execution_contract`, `hepta_execution_transport`, `hepta_tool_protocol` | exclusive | @hepta/contracts / @hepta/architecture |
| `hepta.research.protocol` | current | deterministic replay only | `capability-free-research` | — | exclusive | @hepta/research-validation / @hepta/data-platform |
| `hepta.risk.policy` | current | deterministic risk | `portfolio-risk` | `hepta_risk_core` | exclusive | @hepta/risk / @hepta/execution-safety |
| `hepta.session.runtime` | current | durable session lease and supervisor protocol state | `agent-gateway` | `hepta_session_core` | exclusive | @hepta/session-control / @hepta/gateway |
| `hepta.strategy.runtime` | planned | StrategyProposal only | `untrusted-strategy` | `hepta_strategy_runtime` | exclusive | @hepta/strategy-platform / @hepta/global-allocation |
| `hepta.venue.ctp` | unsupported | none | `execution-authority` | `hepta_venue_ctp` | exclusive | @hepta/venue-ctp / @hepta/execution-core |
| `hepta.venue.ib` | experimental | IB PAPER transport/callback | `execution-authority` | `hepta_ibapi_client`, `hepta_ib_adapter_core` | exclusive | @hepta/venue-ib / @hepta/execution-core |
| `hepta.venue.simulator` | current | deterministic simulated venue | `execution-authority` | `hepta_simulator_venue` | exclusive | @hepta/simulator / @hepta/execution-core |
| `hepta.venue.xt` | unsupported | none | `execution-authority` | `hepta_venue_xt` | exclusive | @hepta/venue-xt / @hepta/execution-core |

`shared-migration` 是待拆分债务，不是允许永久共享所有权。
