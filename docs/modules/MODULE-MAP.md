# Hepta Module Map

Status: generated current view
Applies to: current and target module boundaries
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from module-registry-v2.json

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

| Module | Lifecycle | Authority | Trust domain | Build targets | Ownership | DRI / backup | Technical guide |
|---|---|---|---|---|---|---|---|
| `hepta.agent.support` | current | decision lease client and event hub | `trusted-local` | `hepta_agent_execution_support` | exclusive | @hepta/agent-runtime / @hepta/execution-core | [`modules/technical/hepta-agent-support.md`](technical/hepta-agent-support.md) |
| `hepta.client.runtime` | current | CLI/native/MCP request encoding | `unprivileged-client` | `hepta_native_tool_client`, `heptactl`, `hepta_sessionctl` | exclusive | @hepta/sdk / @hepta/gateway | [`modules/technical/hepta-client-runtime.md`](technical/hepta-client-runtime.md) |
| `hepta.documentation.control` | current | registries/generated views/module and build graph validators | `development` | — | exclusive | @hepta/documentation / @hepta/architecture | [`modules/technical/hepta-documentation-control.md`](technical/hepta-documentation-control.md) |
| `hepta.execution.runtime` | current | only venue mutation/state/OMS/permit authority | `execution-authority` | `hepta_execution_client`, `hepta_execution_server`, `hepta_execution_core`, `hepta_executiond`, `hepta_ib_executiond`, `hepta_paper_terminal_latch_committer`, `hepta_oms_core`, `hepta_state_core`, `hepta_intent_core`, `hepta_allocation_revalidator` | exclusive | @hepta/execution-core / @hepta/execution-safety | [`modules/technical/hepta-execution-runtime.md`](technical/hepta-execution-runtime.md) |
| `hepta.feature.runtime` | current | deterministic feature generations | `feature` | `hepta_feature_runtime` | exclusive | @hepta/data-platform / @hepta/research-validation | [`modules/technical/hepta-feature-runtime.md`](technical/hepta-feature-runtime.md) |
| `hepta.gateway.runtime` | current | identity/session/capability/tool dispatch | `agent-gateway` | `hepta_agent_os_core`, `hepta_tool_gatewayd`, `hepta_trading_tool_core`, `hepta_execution_event_relay_core` | exclusive | @hepta/gateway / @hepta/security-runtime | [`modules/technical/hepta-gateway-runtime.md`](technical/hepta-gateway-runtime.md) |
| `hepta.global.decision` | current | proposal aggregation/global allocation | `global-decision` | `hepta_proposal_aggregator`, `hepta_global_allocator` | exclusive | @hepta/global-allocation / @hepta/risk | [`modules/technical/hepta-global-decision.md`](technical/hepta-global-decision.md) |
| `hepta.management.control` | current | module/config/resource lifecycle | `management` | `hepta_management_control` | exclusive | @hepta/platform / @hepta/architecture | [`modules/technical/hepta-management-control.md`](technical/hepta-management-control.md) |
| `hepta.marketdata.runtime` | current | normalized point-in-time events | `market-data` | `hepta_marketdata_core` | exclusive | @hepta/data-platform / @hepta/venue-ib | [`modules/technical/hepta-marketdata-runtime.md`](technical/hepta-marketdata-runtime.md) |
| `hepta.numeric.core` | current | fixed numeric boundary | `shared-trusted` | `hepta_numeric_core` | exclusive | @hepta/contracts / @hepta/risk | [`modules/technical/hepta-numeric-core.md`](technical/hepta-numeric-core.md) |
| `hepta.observability.runtime` | current | bounded telemetry | `shared-trusted` | `hepta_observability_core` | exclusive | @hepta/reliability / @hepta/platform | [`modules/technical/hepta-observability-runtime.md`](technical/hepta-observability-runtime.md) |
| `hepta.portfolio.compiler` | current | deterministic netting/budget | `portfolio-risk` | `hepta_portfolio_core` | exclusive | @hepta/portfolio / @hepta/risk | [`modules/technical/hepta-portfolio-compiler.md`](technical/hepta-portfolio-compiler.md) |
| `hepta.protocol.contracts` | current | wire/codec | `shared-trusted` | `hepta_execution_contract`, `hepta_execution_transport`, `hepta_tool_protocol` | exclusive | @hepta/contracts / @hepta/architecture | [`modules/technical/hepta-protocol-contracts.md`](technical/hepta-protocol-contracts.md) |
| `hepta.research.protocol` | current | deterministic replay only | `capability-free-research` | — | exclusive | @hepta/research-validation / @hepta/data-platform | [`modules/technical/hepta-research-protocol.md`](technical/hepta-research-protocol.md) |
| `hepta.risk.policy` | current | deterministic risk | `portfolio-risk` | `hepta_risk_core` | exclusive | @hepta/risk / @hepta/execution-safety | [`modules/technical/hepta-risk-policy.md`](technical/hepta-risk-policy.md) |
| `hepta.session.runtime` | current | durable session lease and supervisor protocol state | `agent-gateway` | `hepta_session_core` | exclusive | @hepta/session-control / @hepta/gateway | [`modules/technical/hepta-session-runtime.md`](technical/hepta-session-runtime.md) |
| `hepta.simulation.runtime` | current | simulation-only multi-agent orchestration without broker mutation authority | `simulation-control` | `hepta_multi_agent_simulator` | exclusive | @hepta/simulator / @hepta/global-allocation | [`modules/technical/hepta-simulation-runtime.md`](technical/hepta-simulation-runtime.md) |
| `hepta.strategy.runtime` | current | StrategyProposal only | `untrusted-strategy` | `hepta_strategy_runtime` | exclusive | @hepta/strategy-platform / @hepta/global-allocation | [`modules/technical/hepta-strategy-runtime.md`](technical/hepta-strategy-runtime.md) |
| `hepta.venue.ctp` | unsupported | none | `execution-authority` | `hepta_venue_ctp` | exclusive | @hepta/venue-ctp / @hepta/execution-core | [`modules/technical/hepta-venue-ctp.md`](technical/hepta-venue-ctp.md) |
| `hepta.venue.ib` | experimental | IB PAPER transport/callback | `execution-authority` | `hepta_ibapi_client`, `hepta_ib_adapter_core` | exclusive | @hepta/venue-ib / @hepta/execution-core | [`modules/technical/hepta-venue-ib.md`](technical/hepta-venue-ib.md) |
| `hepta.venue.simulator` | current | deterministic simulated venue | `execution-authority` | `hepta_simulator_venue` | exclusive | @hepta/simulator / @hepta/execution-core | [`modules/technical/hepta-venue-simulator.md`](technical/hepta-venue-simulator.md) |
| `hepta.venue.xt` | unsupported | none | `execution-authority` | `hepta_venue_xt` | exclusive | @hepta/venue-xt / @hepta/execution-core | [`modules/technical/hepta-venue-xt.md`](technical/hepta-venue-xt.md) |

`shared-migration` 是待拆分债务，不是允许永久共享所有权。
