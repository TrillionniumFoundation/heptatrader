# HeptaTrader Agent-native trading runtime architecture

Status: current runtime contract for `0.1.0-beta.1`.

## Trust and data path

```text
Agent / Codex / external client
        |
        | MCP / heptactl / native typed protocol
        v
Tool Gateway
        | peer UID + token + session + capability + schema
        v
Execution Service
        | risk + durable journal + command id + fencing + reconcile
        v
Deterministic Simulator / qualified IB PAPER
```

Agent 可以产生 forecast、target exposure 或有界 TradeIntent，但不拥有 Broker session、订单 ID、最终风控、订单状态机、authoritative position、持久化真相或 kill switch。

## Components

### Agent entry

`adapters/mcp/hepta_mcp_server.py`、`heptactl` 和 native client 只负责发现、编码、调用与解析。MCP bridge 校验固定 UID、私有 token、协议版本和 schema hash。它不能把未知字段或未经校验的 mutation 直接转发给 Broker。

### Tool Gateway

`HeptaTrade/tool_host/` 和 `HeptaTrade/tools/` 校验 peer identity、session、capability、effect、schema 和 request bounds。Gateway 只使用 Execution client contract；构建后的禁止符号门禁确保其不链接 Broker adapter、OMS writer 或 privileged Execution implementation。

### Execution Service

`HeptaTrade/execution/`、`risk/`、`state/`、`reconcile/` 与 `oms_journal*` 形成唯一交易 authority，负责：

- deterministic pre-trade risk；
- journal-before-send 与 command-id 幂等；
- owner/lease/service epoch fencing；
- order/cancel/flatten 生命周期；
- Broker callback 投影；
- authoritative snapshot 与 reconciliation；
- outcome-uncertain 恢复和 terminal latch。

### Venue boundary

- Simulator 是默认实现，支持核心开发与故障测试。
- IB PAPER 仅在 `HEPTA_ENABLE_IBAPI=ON` 构建，并需要受控资格认证。
- CTP 与 XT/QMT 在公开树中没有完整授权 transport，所有 outbound 调用 fail closed。
- IB LIVE 未实现为已认证 capability。

## Mutation invariant

```text
intent
  -> peer/session/capability/schema validation
  -> normalized typed request
  -> quote/position/config freshness
  -> deterministic risk and execution permit
  -> durable journal
  -> venue send
  -> callback projection
  -> authoritative reconciliation
```

任何阶段不确定都不能被解释为成功。网络 timeout、进程重启或响应丢失后，不允许生成新的幂等键重发相同 mutation。

## OS boundary

- 每个不互信 Agent 使用独立 UID、socket、token 和 trust-domain 配置。
- Gateway 与 Execution 使用不同 UID；Broker-owning IB daemon 使用专用 UID。
- systemd units 默认 `NoNewPrivileges`、严格文件系统保护、最小地址族和空 capability set。
- Broker egress policy 只允许指定 execution UID 访问 loopback PAPER 端口。
- credential 通过 systemd credentials 注入，不进入 env 文件。

## Verification boundary

普通 PR 必须通过核心 CI、sanitizer、安装树和 package 门禁。IB PAPER 资格认证是独立的手工 workflow，只能在带 vendor SDK、PAPER Gateway、隔离账户和外部审核 harness 的受控 runner 上执行。没有该证据时，代码可构建不等于 Broker 运行已认证。
