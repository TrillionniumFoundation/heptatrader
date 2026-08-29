# HeptaTrader

HeptaTrader 是一个面向 Agent/AI 的确定性量化交易运行时。仓库只维护能够直接改善研究、决策、风控和执行迭代的代码；发布认证、source bundle、round manifest、evidence closure、安装树证明和强制 CI 不再进入源码路径。

## 当前核心

- Tool Gateway 与受限 Agent 会话
- 确定性 Execution Service、OMS journal、幂等与 fencing
- broker/venue 适配边界
- pre-trade risk、kill switch、reconciliation 与 authoritative snapshot
- simulator、shadow/paper 策略运行脚本
- 市场上下文、策略回放和决策 receipt

Agent 不直接持有 broker session，LLM 不拥有订单状态机、最终风控、对账或 kill switch。

## 快速开发循环

```bash
./scripts/dev_core.sh
```

该脚本只做三件事：IB-disabled 配置、构建 19 个核心测试二进制、运行 `core` CTest 标签。它不会生成 source archive、VM bundle、release manifest、evidence index、安装树或 rootful systemd 认证环境，也不会成为 PR 的强制前置流程。

手动等价命令：

```bash
cmake -S . -B build/core \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_BUILD_LEGACY_MONOLITH=OFF \
  -DHEPTA_BUILD_LEGACY_SIMULATOR=OFF
cmake --build build/core --target hepta_core_test_binaries --parallel 2
ctest --test-dir build/core --output-on-failure --parallel 2 -L core
```

## 保留的安全边界

以下约束属于交易内核，不属于发布仪式：

- journal-before-send
- command ID 幂等与 execution fencing
- Agent、Gateway 与 broker authority 隔离
- bounded framing、peer credential 与 token 文件约束
- broker authoritative reconciliation
- fail-closed risk、kill switch 与安全恢复
- Gateway 禁止链接 broker/credential 权限符号的边界检查

## 目录

```text
HeptaTrade/     核心运行时、OMS、风险、执行、Agent 工具与 venue adapter
strategies/     版本化策略定义
configs/        paper/shadow 配置
adapters/mcp/   MCP 入口
scripts/        运行、研究、故障注入与恢复工具
tests/          19 个快速核心测试
docs/           当前架构、交易协议和运行手册
systemd/        核心运行服务定义
```

## 不再进入本仓库的内容

- release manifest、round 编号快照和 source baseline
- source/runtime/vendor bundle closure
- evidence set/index/ingestion receipt
- install-tree、VM、rootful systemd/AppArmor certification
- repository layout/code-quality policy manifest
- P1 watch/shadow/paper canary、soak、attestation 和 terminal witness 编排
- 发布 readiness、go/no-go、freeze、rollout 和 round 状态文档

如需对外分发制品，应由独立、按需触发的发布流程消费固定 commit；不得重新阻塞核心 OS 的开发循环。
