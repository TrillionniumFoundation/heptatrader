# HeptaTrader

Status: current
Applies to: repository entry point and capability overview
Verification: `./scripts/dev_core.sh` and exact-revision CI
Authority: repository entry point

HeptaTrader 是面向 AI Agent 的模型无关、确定性交易控制与执行运行时，并包含能力隔离的可复现研究平面。模型、策略、MCP、全局优化和管理控制均不能拥有 Broker session、账户真相、最终风险、OMS、对账或 venue mutation；这些始终属于 Execution Authority。

## Current truth

- deterministic Simulator、typed Gateway/native/MCP、session/capability、OMS journal、idempotency/recovery、authoritative snapshot、target-position intent、risk 和 PortfolioCompiler core 已存在；
- global multi-Agent allocator、market/feature data plane 和 management lifecycle 是版本化 target contracts，尚未实现；
- IB PAPER 是 external qualification 前的 conditional 能力；
- CTP、XT/MiniQMT 和所有 LIVE mutation 均 unsupported/fail-closed。

唯一文档入口是 [`docs/README.md`](docs/README.md)。权威能力视图、模块地图、契约索引和路线图均由机器注册表生成；旧 PLAN、旧路径 alias 和历史开发文档不在当前工作树保留。

## Development

```bash
./scripts/dev_core.sh
```

该入口验证 generated views、documentation control plane、repository/module/schema contracts、research determinism、Release core build、CTest 和 Python tests。

## Repository boundaries

```text
HeptaTrade/       active runtime and module-extraction source
adapters/mcp/     unprivileged MCP translation
schemas/          current machine schemas
research/         capability-free research/replay
scripts/          validators and runtime/development tools
systemd/          current deployment templates
tests/            unit, contract, integration and fault tests
docs/             single current Documentation Control Plane V2
legacy/           quarantined inactive source only; no docs or active dependency
```

历史版本由 Git history 提供。法律文件与第三方 provenance 按供应链规则保留，但不构成第二套开发文档。
