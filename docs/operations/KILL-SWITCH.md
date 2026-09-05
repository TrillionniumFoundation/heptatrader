# Kill Switch 与安全退出

Status: current normative
Applies to: Execution and controlled IB PAPER operations
Verification: kill-switch CTest, file-identity negatives and protected PAPER qualification
Authority: kill-switch authority

Kill switch engagement 必须立即阻断新风险、保留已证明安全的 cancel/strict reduce-only/flatten、记录 transition reason/epoch/time、使所有新 permit/plan 失效，并在 restart 后保持，直到 operator 明确解除。策略、Agent、Global Decision、Management 和 venue callback 不能解除 Execution kill switch。

## Canonical PAPER control object

`hepta-execution-ib-paper.service` 只读观察：

```text
control directory: /run/hepta/ib-paper-control
marker:            kill-switch
service identity:  hepta-ib-exec:hepta-ib-exec
```

生产读取器要求：

- service 必须以非 root 身份运行；
- control directory 为 `root:hepta-ib-exec`, mode `0750`, stable inode，且自身 link count 为 2；
- marker 为 `root:hepta-ib-exec`, single-link regular file, mode `0440`，与目录同一 filesystem；
- directory 或 marker 的 symlink/hardlink、owner/group/mode/inode/link-count 不匹配、读取 I/O 失败、观察期间变化、目录替换和 marker 状态竞态全部归为 `Uncertain`；
- `Uncertain` 与 `Engaged` 一样阻断风险增加；目录 identity 失效会在该进程生命周期内永久 latch，必须重启并重新建立边界。

## One-time host preparation

仅由受控 root/operator 路径创建目录，Execution、Gateway、Agent 和普通部署用户无写权限：

```bash
sudo install -d -o root -g hepta-ib-exec -m 0750 \
  /run/hepta/ib-paper-control
sudo stat -Lc 'type=%F owner=%U group=%G mode=%a links=%h dev=%d inode=%i' \
  /run/hepta/ib-paper-control
```

预期：directory、owner `root`、group `hepta-ib-exec`、mode `750`、links `2`。不匹配时不要启动或恢复 PAPER。

## Engage

事故处置或维护开始时，在同一 control directory 内创建临时 regular file，再原子 rename 为 canonical marker：

```bash
set -euo pipefail
control=/run/hepta/ib-paper-control
tmp="$(sudo mktemp --tmpdir="$control" .kill-switch.XXXXXX)"
sudo chown root:hepta-ib-exec "$tmp"
sudo chmod 0440 "$tmp"
sudo mv -T "$tmp" "$control/kill-switch"
sudo stat -Lc 'type=%F owner=%U group=%G mode=%a links=%h dev=%d inode=%i' \
  "$control/kill-switch"
```

预期：regular file、owner `root`、group `hepta-ib-exec`、mode `440`、links `1`。`mv -T` 必须发生在同一目录/filesystem；不要通过 `/tmp`、symlink 或跨设备 copy 建立 marker。

Engage 后验证三层事实：

```bash
sudo systemctl show hepta-execution-ib-paper.service \
  -p ActiveState -p SubState -p MainPID -p Result
sudo journalctl -u hepta-execution-ib-paper.service \
  --since '-5 minutes' --no-pager
heptactl call system.get_health
```

`systemctl active` 只证明进程状态；`journalctl` 只用于诊断。接受条件是 structured health/Execution evidence 显示 kill switch engaged 或 uncertain、new-risk gate closed，且没有新 permit/风险增加命令。观察不到明确关闭状态时按 engaged/incident 处理，不得继续交易。

## Safe-exit while engaged

Kill switch 不是“停止一切”开关。以下动作仅在各自 authority proof 成立时保留：

- cancel：绑定当前 owner、order identity、execution epoch/fence；
- strict reduce-only：exact fixed-point signed position 不跨零，gross exposure 逐 microunit 严格下降；
- flatten：来自 authoritative snapshot、受控 operator/permit、当前 venue/session，并通过 terminal/reconciliation guard。

Rate limit、普通 admission queue 或新风险关闭不能饿死已证明的 safe-exit lane；但任何无法证明为安全退出的请求仍拒绝。

## Disarm

仓库不提供自动 disarm、one-shot campaign 脚本或 Agent tool。Disarm 前必须由独立 operator 记录并确认：

1. exact binary、config source/canonical digest 和 PAPER qualification identity；
2. journal writable/not poisoned，replay 无错误；
3. Broker connection/session/client identity 正确；
4. authoritative orders、positions、executions、cash/risk inputs 完整；
5. reconciliation 为 resolved，所有 uncertain command 已终结或保持隔离；
6. current execution epoch/fence 与所有 permit/plan 一致；
7. risk limits、account/instrument mapping 和 quote freshness 通过；
8. 没有仍要求 kill switch 的 P1/rollback 条件。

满足后只由受控 root/operator 原子删除 marker：

```bash
set -euo pipefail
control=/run/hepta/ib-paper-control
sudo test -f "$control/kill-switch"
sudo unlink "$control/kill-switch"
sudo test ! -e "$control/kill-switch"
```

随后重复 health、journal、authoritative snapshot 和 reconciliation 验证。marker 缺失只表示 `Disarmed` 输入；它本身不打开 new-risk gate。任何目录 identity 变化、第一次/第二次 absence observation 不一致或 health 不确定都保持 gate closed，并重新 engage。

## Evidence to preserve

每次 engage/disarm 至少保存：exact source/binary/config identity、operator ticket/identity、目录和 marker metadata、Execution epoch/fence、journal health、authoritative snapshot/reconcile result、transition time/reason，以及前后 `hepta_kill_switch_transitions_total`/相关 reason-code evidence。不得保存 raw credential、token 或完整 account ID。
