# Product Scope

Status: current normative
Applies to: repository-wide product claims and roadmap boundaries
Verification: capability registry, install tree and exact-revision evidence
Authority: product definition

HeptaTrader 当前产品是 **Agent-compatible deterministic trading control and execution runtime**，附带 capability-free deterministic research/replay。模型是可替换客户端，不是 Broker authority。

## Current core

- typed local Gateway/native/MCP、identity/session/capability enforcement；
- deterministic Simulator；
- OMS journal-before-send、stable command ID、replay/uncertain recovery；
- Execution epoch/fencing、authoritative state/snapshot、target-position intent/permit；
- deterministic risk 与 Simulator/core PortfolioCompiler；
- bounded observability、fault/replay 和最小安装路径。

## Target product

目标是模块化 monorepo + 按 trust domain 分进程的 Hepta system：Market/Feature、isolated Strategy、Global Decision、Management Control 与唯一 Execution Authority 形成可验证闭环。每个模块可以独立迭代，但全局资本、风险、成本和资源约束由 Global Decision/Execution 分层整合。

## Not current capability

- production multi-Agent allocator、dynamic leverage/learned sizing；
- active market-data/feature runtime；
- module rollout control service；
- 多账户/跨 venue 全局优化；
- 自动 SHADOW→PAPER/LIVE promotion；
- CTP、XT/MiniQMT 或任何 LIVE execution。

IB PAPER 是 conditional：只有与 exact artifact/config/harness/session 绑定的外部 qualification 可把该特定构建视为 qualified。目录、代码、示例或单元测试存在不能提升能力状态。

命名规则：当前使用 “deterministic Agent trading runtime”；只有 M4/M5 闭环后可称 “AI-native quantitative trading system”；只有完整 multi-Agent lifecycle/evaluation/capital allocation 后才使用 “Agent OS”。
