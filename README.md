# HeptaTrader

HeptaTrader 是一个**面向 AI Agent 的确定性交易控制与执行运行时**。Codex、OpenClaw 或其他 Agent 通过 MCP/native client 提交查询和有界交易意图；订单状态机、最终风险判断、账户与持仓真相、broker session、幂等、对账和 kill switch 始终由确定性运行时掌握。

> 当前项目不是“LLM 直接下单框架”，也不把某个模型绑定为交易 authority。Codex 是可替换的 Agent 编排客户端，HeptaTrader 是模型无关的交易控制平面。

## 当前能力

| 能力 | 状态 | 说明 |
|---|---|---|
| Tool Gateway / typed Unix protocol | Implemented | peer identity、session、capability、schema 与有界消息校验 |
| Execution Service / OMS journal | Implemented | journal-before-send、command-id 幂等、fencing、uncertain recovery |
| Deterministic simulator | Implemented | 本地执行链路、回放与故障测试；不等同于真实 venue 认证 |
| IB PAPER | Experimental | 具备独立 broker authority、风险限制、kill switch、reconciliation |
| CTP adapter | Scaffold / fail-closed | 未绑定真实 transport，不允许报告连接成功 |
| XT/QMT adapter | Scaffold / fail-closed | 未绑定真实 transport，不生成伪 broker ACK 或本地伪订单号 |
| LIVE | Unsupported | 不提供默认或已认证的 LIVE authority |
| Codex / OpenClaw | Client adapters | 通过 MCP 使用当前 session 暴露的工具，不拥有 broker credential |
| EURUSD SHADOW research | Experimental | 研究与回放管线，不授权 PAPER/LIVE mutation |

完整矩阵见 [`docs/CAPABILITY-MATRIX.md`](docs/CAPABILITY-MATRIX.md)。

## 运行时边界

```text
Codex / Agent / Operator
          |
          | MCP / heptactl / native client
          v
Tool Gateway
          |
          | authenticated typed Unix protocol
          v
Execution Service
          |
          | deterministic venue contract
          v
Simulator / IB PAPER
```

不可破坏的 invariant：

1. 只有 Execution Service 可以向 venue 发送订单。
2. Agent 和 Gateway 不持有 broker credential，不直接连接 broker API。
3. mutation 在外部发送前进入 durable journal。
4. uncertain retry 必须复用原 command ID，payload 变化触发幂等冲突。
5. 过期 session、owner 或 lease 不能继续增加风险。
6. 持仓与活动订单以 authoritative venue/Execution 投影为准。
7. 身份、协议、quote、配置、持久化或 kill switch 不确定时 fail closed。
8. 合法 cancel、reduce-only 与 flatten 退出路径保持可用。

## 快速开发循环

```bash
./scripts/dev_core.sh
```

该入口配置 IB-disabled 构建、编译核心测试并运行 `core` CTest；同时运行轻量 Python contract tests。它不生成 round、evidence closure、安装树证明或发布认证制品。

也可以使用 CMake preset：

```bash
cmake --preset core-release
cmake --build --preset core-release
ctest --preset core-release
```

## 最小运行时安装

```bash
cmake --preset core-release
cmake --build --preset core-release
cmake --install build/core-release --component runtime
```

安装只包含运行时二进制、MCP bridge/launcher、固定 systemd unit、tmpfiles 与示例配置；签名、SBOM、宿主合规和正式发布制品应由独立、按需触发的发布流程消费固定 commit。

## 仓库结构

```text
HeptaTrade/       当前 C++ Gateway、Execution、OMS、risk、state 与 venue runtime
adapters/mcp/     MCP bridge
plugins/          Codex/OpenClaw 插件元数据
strategies/       版本化策略定义
scripts/          开发、配置、研究与最小运行时工具
systemd/          固定 Gateway、Simulator、IB PAPER 服务定义
tests/            核心 contract/integration tests
docs/             current contracts、runbooks、experimental plans
legacy/           待迁出主运行面的历史系统（迁移过程中）
```

## 文档入口

从 [`docs/README.md`](docs/README.md) 开始。所有 current 文档必须描述仓库中实际存在的代码、命令和路径；未来设计放入 proposal/experimental 状态，不得伪装成已实现能力。
