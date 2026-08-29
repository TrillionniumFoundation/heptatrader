# HeptaTrader AI-Native Trading Runtime Architecture

Status: current runtime contract.

本文只描述当前代码中的交易运行时。发布包、round、P1、认证、evidence closure、动态 PAPER campaign 和宿主证明不属于本架构。

## 1. 数据与调用路径

```text
Agent / Codex / OpenClaw
        |
        | MCP / heptactl / native client
        v
Tool Gateway
        |
        | typed authenticated Unix protocol
        v
Execution Service
        |
        | deterministic venue contract
        v
Simulator / IB / CTP / XT adapter
```

Agent 可以提交查询和有界交易意图，但不拥有 broker session、订单状态机、最终风险判断、持久化执行状态或对账真相。

## 2. 组件职责

### Agent entry

- `adapters/mcp/hepta_mcp_server.py`
- `HeptaTrade/cli/heptactl*`
- `HeptaTrade/client/native_tool_client*`

入口只做工具发现、请求编码和结果解析，不得出现第二条 broker path。

### Tool Gateway

- `HeptaTrade/tool_host/`
- `HeptaTrade/tools/`

Gateway 校验 peer identity、session、capability、schema 和参数，并把 mutation 转发给 Execution Service。它不链接 broker adapter，不持有 broker credential。

### Execution Service

- `HeptaTrade/execution/`
- `HeptaTrade/oms_journal*`
- `HeptaTrade/state/`
- `HeptaTrade/risk/`

Execution Service 是唯一订单 authority，负责 journal-before-send、command-id 幂等、fencing、确定性风控、订单生命周期、authoritative snapshot、reconciliation 和 uncertain recovery。

### Venue adapters

- `HeptaTrade/simulator/`
- `HeptaTrade/adapter_ib/`
- `HeptaTrade/adapter_ctp/`
- `HeptaTrade/adapter_xt/`

adapter 只翻译 venue 协议和事件，不决定策略、资本分配或风险政策。

## 3. 固定运行模式

### Simulator

`hepta-executiond` 使用确定性 simulator venue，适合本地开发、回放和故障测试。

### IB PAPER

`hepta-ib-executiond` 是固定的 broker-owning PAPER authority。它通过独立 OS identity、文件系统 kill switch、broker network policy、credential、journal 和 reconciliation 运行。

仓库不再提供动态 PAPER domain、campaign open/close、renew、repair、attestation 或 finalizer 编排。session 由 operator 使用 `hepta-sessionctl` 显式 provision/revoke；部署侧负责安全创建 token 文件。

### LIVE

LIVE 不是默认或已认证能力。任何未来 LIVE 路径必须复用同一 Execution authority、risk、journal、fencing 和 reconciliation，不得从 Agent 或 legacy monolith 旁路进入。

## 4. 不可破坏的 invariant

1. 只有 Execution Service 可以向 venue 发送订单。
2. Agent/Gateway 不持有 broker credential，也不能直接连接 broker API。
3. mutation 在外部发送前进入 durable journal。
4. uncertain retry 复用原 command ID。
5. 过期 session、owner 或 lease 不能继续增加风险。
6. 持仓和活动订单以 venue/Execution 投影为准。
7. 协议、身份、quote、配置、持久化或 kill switch 不确定时 fail closed。
8. 合法的 cancel、reduce-only 和 flatten 退出路径保持可用。

## 5. 一次 mutation

```text
Agent intent
  -> peer/session/capability check
  -> schema and normalized intent
  -> quote freshness + deterministic pre-trade risk
  -> execution permit + command ID
  -> durable journal
  -> venue send
  -> execution event projection
  -> authoritative reconciliation
```

## 6. 策略边界

策略和 AI 层应输出 forecast、target exposure 或有界 TradeIntent。最终手数、组合净额、订单类型、拆单、撤单和 venue routing 属于确定性 portfolio/risk/execution 层。

legacy monolith 默认关闭；新功能不得继续扩展直接下单式策略 API。

## 7. 开发循环

```bash
./scripts/dev_core.sh
```

该入口只保护会造成交易错误或权限越界的核心 invariant。仓库没有强制 CI、发布证据、安装认证或 round gate。
