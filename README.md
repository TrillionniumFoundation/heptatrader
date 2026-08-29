# HeptaTrader

HeptaTrader 是一个面向 Agent/AI 的确定性量化交易运行时。仓库当前只维护能够直接改善研究、决策、风控和执行迭代的代码；发布认证、source bundle、round manifest、evidence closure 和安装树证明不再进入日常源码与 PR 路径。

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
cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_BUILD_LEGACY_MONOLITH=OFF \
  -DHEPTA_BUILD_LEGACY_SIMULATOR=OFF

cmake --build build --target hepta_core_test_binaries --parallel 2
ctest --test-dir build --output-on-failure --parallel 2 -L core
```

PR CI 只执行上述核心路径。不会构建 source archive、VM bundle、release manifest、evidence index、安装树或 rootful systemd 认证环境。

## 安全边界

以下约束仍属于交易内核，不能为了提速绕过：

- journal-before-send
- command ID 幂等与 execution fencing
- Agent、Gateway 与 broker authority 隔离
- bounded framing、peer credential 与 token 文件约束
- broker authoritative reconciliation
- fail-closed risk、kill switch 与安全恢复

## 目录

```text
HeptaTrade/     核心运行时、OMS、风险、执行、Agent 工具与 venue adapter
strategies/     版本化策略定义
configs/        paper/shadow 配置
adapters/mcp/   MCP 入口
scripts/        运行、研究、故障注入与恢复工具；不含发布认证工具链
systemd/        核心运行服务定义；不含 P1/round 认证编排
tests/          快速核心测试集
docs/           当前架构、交易协议和运行手册
```

## 发布边界

源码仓库不再保存或生成以下对象：

- `release-manifests/` 与 round 编号快照
- `source-baseline.json`
- source/runtime/vendor bundle closure
- evidence set/index/ingestion receipt
- install-tree、VM、rootful systemd certification
- repository layout/code-quality policy manifests

将来需要可分发制品时，应由独立、按需触发的发布仓库或外部流水线从固定 commit 生成，不得重新阻塞核心 OS 的 PR 和开发循环。
