# Incident runbook

适用于连接、订单、journal、风控、身份、安装或资格认证异常。

## Immediate containment

1. engage kill switch 或保持只读，停止新增风险；
2. 不删除、不截断、不手工编辑 OMS journal；
3. 保存 alerts、metrics、journal、systemd logs、安装 manifest、SBOM、provenance、配置 hash、service/connection epoch；
4. 记录 UTC 时间线、账户、venue、影响订单和当前 authoritative state。

## Classification

- **P1/SEV-1**：状态不一致、outcome uncertain、journal malformed/poisoned、重复 event ID、权限边界失败、连续 Broker 拒单或无法安全退出。
- **P2/SEV-2**：行情/回调停滞、延迟或错误率显著恶化，但 authoritative state 已知且风险受控。
- **P3/SEV-3**：无交易影响、可确定恢复的单次异常。

## Diagnosis

```bash
systemctl --no-pager --full status <affected-unit>
journalctl -u <affected-unit> --since -30min
cat /var/lib/<service-state>/heptatrader-alerts.json
```

- 连接异常：核对 loopback endpoint、Gateway 状态、egress policy、connection epoch；不要改成公网地址。
- 201/reject：核对账户权限、instrument identity、side/quantity/price、reference quote 和 stable risk code。
- outcome uncertain：查询 authoritative open orders、fills 和 positions；禁止使用新 command ID 重发。
- journal/路径异常：停止相关 daemon，保留 inode/metadata 证据并从上一个已验证状态恢复。
- CI/package 异常：不得手工跳过门禁发布。

## Recovery

只有 authoritative reconciliation 完成、根因已修、相同故障回归通过、安装/配置重新验证且 owner 批准后，才可以 disarm。先恢复 read-only，再恢复 bounded PAPER mutations。复盘必须包含根因、影响、证据、修复 commit、测试与防复发门禁。
