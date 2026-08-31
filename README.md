# HeptaTrader

Status: current
Applies to: repository entry point and capability overview
Verification: `./scripts/dev_core.sh` and exact-revision CI
Authority: repository entry point

HeptaTrader 是面向 AI Agent 的模型无关、确定性交易控制与执行运行时，并包含能力隔离的可复现研究平面。模型、策略和 MCP 是可替换客户端；Execution Authority 始终拥有 Broker session、OMS、最终风险、权威状态、对账与安全退出。

## 当前能力边界

- Deterministic Simulator、typed Gateway、native/MCP client、session/capability enforcement、OMS journal、idempotency/recovery、authoritative snapshot、target-position intent 和 deterministic risk 属于当前 core。
- PortfolioCompiler 属于 Simulator/core 的可信纯策略边界；生产 multi-Agent allocator 仍是计划能力。
- IB PAPER 仍是 external qualification 前的 experimental/conditional 能力。
- CTP、XT/MiniQMT 和所有 LIVE mutation 都不支持并 fail closed。

权威能力源为 [`docs/product/capability-registry-v2.json`](docs/product/capability-registry-v2.json)。完整开发入口为 [`docs/README.md`](docs/README.md)，单一全局路线图为 [`docs/program/MASTER-ROADMAP.md`](docs/program/MASTER-ROADMAP.md)。

## 六平面

```text
Research/Replay
Market Data/Feature
Agent/Strategy
Global Decision
Execution Authority
Management Control
```

数据与 mutation 方向：

```text
data -> strategy proposal -> global allocation -> target intent
     -> deterministic risk -> permit -> durable journal -> venue
```

只有 Execution Authority 可以产生 venue mutation。

## 开发入口

```bash
./scripts/dev_core.sh
```

该命令执行 repository truth、documentation control plane、schema/module checks、research verification、Release core build、core CTest 和 Python tests。

## 仓库地图

```text
HeptaTrade/       active C++ runtime and module extraction source
adapters/mcp/     unprivileged MCP adapter
schemas/          current machine schemas
research/         capability-free research runner
scripts/          development and validation tooling
systemd/          active deployment templates
tests/            unit/contract/integration/fault tests
docs/             only current V2 documentation and machine registries
legacy/           quarantined inactive source; no active dependency
```

历史开发文档不保留在 active tree；版本历史由 Git 提供。法律文件、第三方 notice 和 vendor provenance 继续保留。
