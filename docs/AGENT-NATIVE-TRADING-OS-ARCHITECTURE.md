# HeptaTrader AI-Native Trading Runtime Architecture

Status: current architecture.

本文只描述当前代码中的运行时边界。发布包、安装树、round、P1、认证、证据闭环和宿主环境证明不属于本架构，也不得成为核心 OS 迭代的前置条件。

## 1. 系统边界

```text
Agent / Codex / OpenClaw
        |
        | heptactl / MCP / native client
        v
Unix Tool Gateway
        |
        | typed, authenticated local protocol
        v
Execution Service
        |
        | deterministic venue contract
        v
Simulator / IB / CTP / XT adapter
```

Agent 可以提出查询和交易意图，但不拥有 broker session、订单状态机、最终风控、订单 ID、持久化执行状态或对账真相。

## 2. 运行时组件

### Agent entry

- `HeptaTrade/cli/heptactl*`
- `adapters/mcp/hepta_mcp_server.py`
- `HeptaTrade/client/native_tool_client*`

这些入口只负责发现工具、编码请求和解析结果，不得新增直接 broker 路径。

### Tool Gateway

- `HeptaTrade/tool_host/`
- `HeptaTrade/tools/`

Gateway 负责会话认证、能力过滤、参数/schema 校验、调用审计以及把 mutation 转发给 Execution Service。它不链接 broker credential 或 venue 下单权。

### Execution Service

- `HeptaTrade/execution/`
- `HeptaTrade/oms_journal*`
- `HeptaTrade/state/`
- `HeptaTrade/risk/`

Execution Service 是唯一订单 authority，负责 journal-before-send、command ID 幂等、fencing、preview/permit、风控、订单生命周期、authoritative snapshot、reconciliation 和不确定状态恢复。

### Venue adapters

- `HeptaTrade/simulator/`
- `HeptaTrade/adapter_ib/`
- `HeptaTrade/adapter_ctp/`
- `HeptaTrade/adapter_xt/`

adapter 只翻译 venue 协议和事件，不决定策略、资本分配或风险政策。

## 3. 不可破坏的 invariant

1. **单一执行权**：只有 Execution Service 可以向 venue 发送订单。
2. **Agent 无 broker 权限**：Agent/Gateway 不持有 broker credential，不直接连接 broker API。
3. **先记账后发送**：mutation 在外部发送前必须进入 durable journal。
4. **幂等**：不确定重试必须复用原 command ID；新 ID 不得解析旧 mutation。
5. **fencing**：过期 session、owner 或 lease 不得继续增加风险。
6. **authoritative state**：持仓、活动订单和恢复状态以 venue/Execution 投影为准，Agent 不能上传“完整真相”。
7. **fail closed**：协议、身份、配置、kill switch、quote freshness 或持久化状态不确定时，阻断新增风险。
8. **退出路径保留**：在 owner/fencing 合法时，cancel、reduce-only 和 flatten 不应被普通新增风险规则误伤。

## 4. 一次 mutation 的最小流程

```text
Agent intent
  -> session/capability check
  -> schema and normalized intent
  -> current quote + deterministic pre-trade risk
  -> short-lived execution permit + future command ID
  -> durable journal
  -> venue send
  -> execution event projection
  -> authoritative reconciliation
```

Gateway 不得自行签发 permit；Agent 不得自行选择新的 command ID 来重试 uncertain 请求。

## 5. 运行模式

### Simulator

`hepta-executiond` 使用确定性 simulator venue，适合开发、回放和故障测试。它不需要 broker credential。

### IB PAPER

`hepta-ib-executiond` 是独立的 broker-owning PAPER authority。IB API 在普通开发构建中默认关闭；启用时必须显式提供 SDK 源码路径和运行配置。

### LIVE

当前产品不把 LIVE 作为默认或已认证能力。任何未来 LIVE 路径必须复用同一 Execution authority、risk、journal、fencing 和 reconciliation 约束，不能从 Agent 或 legacy monolith 旁路进入。

## 6. 研究与策略边界

策略和 AI 层应输出 forecast、target exposure 或有界 TradeIntent。最终手数、净额、订单类型、拆单、撤单和 venue routing 属于确定性 portfolio/risk/execution 层。

当前仓库仍含 legacy 策略接口和 monolith，但默认构建关闭。新功能不得继续扩展直接下单式策略 API。

## 7. 开发循环

```bash
./scripts/dev_core.sh
```

该入口只构建并运行直接保护运行时 invariant 的核心测试。没有强制 CI、发布证据、安装认证或 round gate。

## 8. 外部部署边界

systemd unit、OS identity、credential placement、firewall 和 broker SDK 由部署环境负责。仓库保留运行时配置示例和安全约束，但不再在源码中证明某台宿主、镜像、VM 或安装树已经“认证”。
