# HeptaTrader

HeptaTrader 是一个面向 Agent/AI 的确定性量化交易运行时。仓库只维护直接服务于研究、策略决策、风控、OMS 和执行的代码；发布认证、round、evidence closure、安装树证明、动态 PAPER campaign 和强制 CI 不再进入核心 OS。

## 核心运行时

- Tool Gateway、typed Unix protocol 与受限 Agent 会话
- Execution Service、OMS journal、command-id 幂等与 fencing
- pre-trade risk、kill switch、authoritative snapshot 与 reconciliation
- simulator、IB PAPER 和 venue adapter
- MCP/native client
- 市场上下文、策略回放、shadow runner 与决策 receipt

Agent 不直接持有 broker session。LLM 不拥有订单状态机、最终风险判断、订单 ID、对账真相或 kill switch。

## 快速开发循环

```bash
./scripts/dev_core.sh
```

该入口只执行 IB-disabled Release 配置、构建核心测试二进制并运行 `core` CTest 标签。它不生成 bundle、manifest、evidence index、安装树、VM 或 rootful systemd 认证环境，也不是 PR 的强制门禁。

## 保留的安全边界

- journal-before-send
- command ID 幂等与 execution fencing
- Agent、Gateway 与 broker authority 隔离
- bounded framing、peer credential 与 session token 约束
- authoritative reconciliation
- fail-closed risk、kill switch 与安全恢复
- Gateway 禁止链接 broker/credential 权限符号
- 固定 IB Execution UID 的 broker 端口网络隔离

## 精简后的运行面

```text
HeptaTrade/     执行、OMS、风险、状态、Agent 工具与 venue adapter
adapters/mcp/   MCP 入口
strategies/     版本化策略定义
scripts/        开发、市场数据、策略回放与最小运行时辅助工具
systemd/        固定 Gateway、Simulator、IB PAPER 服务定义
tests/          快速核心测试
docs/           当前运行时契约
```

运行时不再内置动态 domain/PAPER campaign 的创建、renew、repair、finalizer、attestation 或 witness 编排。部署侧只需启动固定服务、提供 session token、broker credential 和配置；session 可通过 `hepta-sessionctl` 显式管理。

## 仓库外职责

对外分发所需的签名、SBOM、安装包或宿主合规验证，应由独立、按需触发的流程消费固定 commit。它们不得重新成为普通功能提交或策略迭代的前置条件。
