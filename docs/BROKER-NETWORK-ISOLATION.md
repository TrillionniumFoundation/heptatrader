# Broker network isolation

## 目标

Agent 能生成并运行代码，因此“没有 broker credential”还不够：Agent 和 Tool Gateway 也不能直接打开 broker API socket。broker 网络可达性必须是 broker-owning Execution 进程的 OS 能力。

## 当前运行时资产

- `scripts/hepta_broker_egress_policy.py`
- `scripts/check_hepta_broker_network_policy.py`
- `systemd/hepta-broker-egress-policy.service`
- `systemd/hepta-broker-network-policy-v1.json`
- Execution service 的独立 OS identity

这些资产构成运行时边界，不构成宿主或发布认证体系。

## 必须满足的 invariant

1. Agent 与 Tool Gateway 不能直接连接受保护的 broker API 端口。
2. 只有明确配置的 broker-owning Execution identity 可以获得对应可达性。
3. Simulator execution identity 不得继承 broker 网络权限。
4. Tool Gateway 的订单 mutation 只能通过本地 typed Execution 协议进入 broker authority。
5. 策略代码、MCP adapter 和 legacy helper 不得新增第二条 broker path。
6. policy 缺失、解析失败、identity 不匹配或 live rule 状态不确定时，默认拒绝 broker 可达性。

## 受保护的 IB 本地 API 端口

当前策略覆盖常见 TWS/IB Gateway 本地 API 端口：

- `4001`
- `4002`
- `7496`
- `7497`

规则应只约束本机 broker listener，不应误伤 broker 程序自身对外建立的上游会话。

## 进程职责

### Agent

- 无 broker credential
- 无修改 nftables、network namespace 或 service identity 的权限
- 只能使用 MCP/native tool client

### Tool Gateway

- 不链接 venue adapter
- 不持有 broker credential
- 只通过 Unix Execution protocol 转发经过认证的调用

### Execution Service

- 独立 OS identity
- 唯一 broker session owner
- 同时受 pre-trade risk、kill switch、journal、fencing 和 reconciliation 约束

网络 allowlist 不能替代交易风控；交易风控也不能替代网络隔离。

## 失败语义

- policy helper 启动失败：不授予 broker 可达性。
- policy 或 identity 配置漂移：收紧到 deny，而不是保留旧 allow。
- Execution 进程退出：不得让 Agent/Gateway 获得遗留 broker path。
- 无法确认规则状态：视为隔离失败并阻断 PAPER 启动或新增风险。

## 开发与部署边界

普通开发循环不运行 rootful nftables、VM、systemd 或 AppArmor gate。开发者只维护 policy 代码、配置解析和不变量；具体 OS account、firewall 加载和服务启动由部署环境负责。

部署验证如有需要，应在目标宿主上独立执行，不生成 round、manifest、attestation、closure 或源码仓库内的认证制品。
