# Broker network isolation

## 目标

Agent 能生成并运行代码，因此“没有 broker credential”还不够。Agent 和 Tool Gateway 也不能直接连接本地 broker API 端口；broker 网络可达性必须只属于固定的 broker-owning Execution UID。

## 最小实现

- `scripts/hepta_broker_egress_policy.py`
- `systemd/hepta-broker-egress-policy.service`
- `systemd/hepta-broker-network-policy-v1.json`
- 固定 IB PAPER identity `hepta-ib-exec` / UID `2003`

策略是静态的：允许配置中的 UID 访问受保护端口，拒绝所有其他本地 UID；其余 egress 不受影响。没有动态 identity manifest、activation reservation、receipt、ledger、campaign 或 attestation。

## 当前受保护端口

- `4001`
- `4002`
- `7496`
- `7497`

这些是常见 TWS/IB Gateway 本地 API listener。规则只匹配目标端口，不限制 broker 程序连接远端服务。

## invariant

1. Agent 与 Tool Gateway 不能连接受保护 broker API 端口。
2. 只有固定 IB Execution UID 可以连接。
3. Simulator identity 不继承 broker 网络权限。
4. 所有交易 mutation 只能经本地 typed Execution protocol 进入 broker authority。
5. policy 缺失、配置非法或 nft 应用失败时，脚本尝试切换到 deny-all 并返回失败。
6. 网络 allowlist 不能替代 pre-trade risk、kill switch、journal 或 reconciliation。

## 生命周期

`hepta-execution-ib-paper.service` 直接依赖 `hepta-broker-egress-policy.service`。启动 PAPER authority 前先加载规则；停止 policy service 时规则收紧为 deny-all。

部署侧可以把 `hepta-agent-broker-egress-policy.conf.example` 应用于实际承载 Agent 的 service，以确保 Agent 运行时绑定该边界。

普通开发循环不运行 rootful nftables。目标宿主上的规则加载是部署操作，不生成 round、manifest、closure 或源码仓库内的认证制品。
