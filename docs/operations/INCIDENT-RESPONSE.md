# 事故响应

Status: current normative
Applies to: runtime incidents and on-call response
Verification: deterministic fault matrix, incident drills and post-incident review
Authority: incident response authority

事故响应顺序不能由吞吐、PnL、自动恢复或“尽快恢复交易”改变：

1. 保护账户和外部状态；
2. 阻断新风险；
3. 保持已证明安全的 cancel/reduce/flatten；
4. 保存 journal、event、snapshot、config、binary 和 session identity；
5. 建立 Broker/venue authoritative truth；
6. reconcile；
7. 再决定恢复、回滚或持续隔离。

## Severity

### P1 — immediate risk containment

- venue send 没有对应 durable command；
- journal append/sync/replay failure 且 new-risk gate 仍开放；
- order/position/state break 泄漏到 risk allow；
- kill switch、terminal latch、epoch/fence 无法证明；
- stale/incomplete snapshot 被接受；
- uncertain exposure 超过 policy reconcile deadline；
- wrong account/environment/credential/candidate identity；
- unauthorized LIVE/CTP/XT path 或第二 venue mutation path；
- credential/account/token 出现在日志、metric label 或 evidence。

### P2 — degraded but contained

- Broker/venue disconnected，且已知 exposure 被完整隔离；
- backlog、repeated reject、latency regression、series-cap drops；
- one strategy/module quarantined，而 Global Risk/Execution 仍完整；
- non-authoritative telemetry/exporter failure。

无法判断 P1/P2 时按 P1。

## Immediate containment

PAPER 立即按 [Kill Switch runbook](KILL-SWITCH.md) engage marker。Simulator 或通用控制面关闭新 admission，并暂停 Gateway：

```bash
sudo systemctl stop hepta-tool-gateway.service
sudo systemctl show \
  hepta-tool-gateway.service \
  hepta-execution-simulator.service \
  hepta-execution-ib-paper.service \
  -p ActiveState -p SubState -p MainPID -p Result
```

停止 Gateway 不能替代 Execution kill switch；Gateway 断开也不能被解释为 broker mutation 已停止。Execution 进程是否继续运行取决于是否需要 cancel/reconcile/safe-exit。不要在未保存状态前盲目 kill broker-owning进程。

## Establish exact incident identity

创建受权限保护的 evidence directory，不把 secret 写入其中：

```bash
umask 077
incident_root="/var/lib/heptatrader-incidents/$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -o root -g root -m 0700 "$incident_root"
```

记录：

```bash
heptactl --version | sudo tee "$incident_root/version.txt"
sha256sum \
  /usr/local/libexec/heptatrader/hepta-executiond \
  /usr/local/libexec/heptatrader/hepta-tool-gatewayd \
  | sudo tee "$incident_root/binary-sha256.txt"
sudo systemctl show \
  hepta-tool-gateway.service \
  hepta-execution-simulator.service \
  hepta-execution-ib-paper.service \
  -p Id -p ActiveState -p SubState -p MainPID -p ExecMainStatus -p Result \
  | sudo tee "$incident_root/systemd-state.txt"
sudo journalctl \
  -u hepta-tool-gateway.service \
  -u hepta-execution-simulator.service \
  -u hepta-execution-ib-paper.service \
  --since '-30 minutes' --no-pager \
  | sudo tee "$incident_root/journal.txt"
```

实际安装 prefix 以 approved install manifest 为准。Journal 导出前确认目标路径/owner/mode；不要复制正在变化的文件作为“完整证据”。优先使用应用提供的 snapshot/replay/export 协议；若必须复制，先关闭写入或使用 filesystem/application一致性快照，并记录 inode/size/hash 前后稳定性。

## Read-only health and authoritative queries

存在受控 session 时，只运行只读请求：

```bash
export HEPTA_TOOL_SOCKET=/run/hepta-agent/tools.sock
export HEPTA_TOOL_SESSION_TOKEN='<controlled-injection>'
heptactl call system.get_health
heptactl call orders.list
heptactl call portfolio.list_positions
heptactl call account.get_summary
heptactl call risk.get_limits
```

不要把 token 写入 evidence。Gateway 结果只有在 session/epoch/currentness 可验证时才可使用；Gateway 不可用时直接使用 Execution/Broker 的受控运维接口，不得建立新的原始下单路径。

## Triage decision tree

### Journal or durability fault

- 立即保持 new-risk closed；
- 检查 `hepta_oms_journal_failures_total`、`hepta_oms_journal_poisoned`、filesystem容量/只读状态和 journal identity；
- 禁止删除、截断、重建或从空文件启动；
- 若 send 可能发生但 durable outcome 不明确，将相关 command 标记 uncertain，进入 reconciliation；
- 只有 replay、digest、sequence和 venue truth 收敛后才能恢复。

### State/snapshot/fencing fault

- invalidate 当前 permit/plan/session；
- 保留 exact execution epoch、fencing generation、snapshot generation/digest；
- fence 旧 writer，拒绝 caller-time 或缓存 snapshot；
- 重新取得 authoritative venue snapshot并 reconcile；
- generation rollback 或 issuer identity不明时继续隔离。

### Broker disconnect/uncertain send

- 不生成新 command ID，不盲目 retry；
- 查询原 command ID 的 durable outcome、venue correlation、open orders 和 executions；
- disconnect/reconnect 期间所有风险增加保持关闭；
- 复用原 identity 解决 uncertain，不以 client timeout 推断未成交。

### Credential or account mismatch

- engage kill switch，停止新 mutation；
- revoke/rotate credential through deployment authority；
- 不在日志或 ticket 中粘贴 raw value；
- 绑定 host/account/session fingerprint 重新 qualification；
- 任何 LIVE authority出现都属于 capability violation。

## Safe-exit decision

Safe-exit 不是自动授权。每个 cancel/reduce/flatten 都要证明 current owner、order/instrument/account、epoch/fence、authoritative snapshot、strict fixed-point reduction 和 venue规则。无法证明时保持隔离并升级人工决策；不能通过关闭 risk、改 command ID 或直接调用 broker API“救场”。

## Recovery gates

恢复前必须满足：

- incident cause 已被隔离或回滚；
- exact artifact/config identity 已知且允许当前环境；
- journal可写/not poisoned，replay完整；
- authoritative orders/positions/executions/cash inputs 已收集；
- reconcile resolved，uncertain commands 不再泄漏风险；
- new execution epoch/fence 已建立，旧 capability 已失效；
- kill switch只能在独立 operator检查后解除；
- startup/readiness runbook 从头执行；
- P1 telemetry不再新增且安全退出 lane通过探针。

只恢复 Gateway 或 systemd service 不代表恢复交易权限。

## Closeout

Post-incident record 至少包括：UTC timeline、detect/contain/reconcile/recover timestamps、exact source/binary/config identities、affected authority、commands/events、root cause、why gates did/did not hold、negative test、code/doc/runbook changes、owner和防复发验收。Secret、raw account和token必须 redact；evidence hash/size/owner/mode 与访问记录保留。

事故期间永久禁止：删除 Journal、切换 command ID、手改权威状态、伪造 callback/status、降低 risk、启用 LIVE/CTP/XT、建立绕过 Gateway/Execution 的第二 mutation path、管理员跳过 qualification 或把历史绿色结果当作当前证据。
