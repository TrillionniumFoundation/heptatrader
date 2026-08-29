# RUNBOOK-KILLSWITCH

## 适用边界

本页原有的 `HEPTA_GLOBAL_KILL_SWITCH` / `HEPTA_FLATTEN_ONLY` 操作仅适用于
legacy 单进程执行路径，不能作为新的独立 IB PAPER execution authority 的
控制方式。`hepta-ib-executiond` 不接受环境变量形式的 PAPER kill switch。

新的 canonical PAPER authority 使用固定控制面：

- 目录：`/run/hepta/ib-paper-control`，必须为 `root:hepta-ib-exec 0750`。
- marker：`kill-switch`，必须为 single-link regular file，
  `root:hepta-ib-exec 0440`。
- marker 存在表示 engaged；缺失只有在 pinned directory identity 连续安全时才
  表示 disarmed；权限、owner、inode、symlink 或 I/O 状态不确定时一律阻断新增风险。
- execution service 对该目录只有读权限，不能自行解除 kill switch；cancel 风险退出
  仍保留 owner/fencing 校验后可用。
- tmpfiles 声明默认创建 engaged marker。当前阶段不得解除；只有 provisioned-host
  集成门、最小限额审核和单独 PAPER 授权全部通过后，才可制定 root operator 的
  原子解除/恢复步骤。
- Round19 的 broker-free systemd rehearsal 只允许验证 tmpfiles 首次/重复创建、marker
  inode 稳定、service mount namespace 只读和 service/root mutation 失败；整个过程
  marker 必须保持 engaged。Docker rehearsal 即使为绿也不构成上述“provisioned-host
  集成门”通过，native disposable-VM 门之前仍禁止解除。

## Capped PAPER one-shot operator seam

达到 provisioned-host、最小限额和单独 PAPER cycle 授权后，只允许 root 通过
`hepta-ib-paper-domain-authority` 的 one-shot 控制面建立短窗口。Agent skill、Gateway、
Execution service 和 broker adapter 都不得直接修改 marker。

约束：

- `--cycle-id` 与 canonical TradeIntent 的 `sha256:` digest 必须同时提供；root receipt
  永久绑定二者。
- `--operator-ttl-sec` 只能是 5–20 秒。超过窗口由 systemd transient watchdog 自动
  re-engage；人工 re-engage 应在 `place` 返回后立即执行，不等待成交。
- operator 先把 watchdog timer 注册到 PID 1 并确认 active，然后才对已验证的精确
  marker inode 做 `unlinkat`。timer 未确认时 marker 保持 engaged。
- re-engage 使用 pinned control-directory FD 原子发布新的 single-link
  `root:hepta-ib-exec-<domain> 0440` marker，并 fsync 文件和目录。
- runtime lease 只写入 `/run/hepta/ib-paper-one-shot`（`0700 root:root`）；持久 receipt
  只写入 `/var/lib/hepta-ib-paper-one-shot`（`0700 root:root`），均不得包含 token、
  permit、账户号或 broker credential。
- 即使 runtime lease 丢失或损坏，watchdog re-engage 也必须先恢复 marker，再以失败
  状态退出；Execution 将任何不确定 marker 状态视为阻断新增风险。

示例（仅在独立 operator 审批已经成立后）：

```bash
sudo /usr/libexec/hepta-ib-paper-domain-authority \
  --operator-disarm --domain alpha \
  --cycle-id capped-paper-example-v1 \
  --intent-sha256 sha256:<64-hex> --operator-ttl-sec 20

# Agent 只能在该窗口内通过 canonical risk.preview_order -> trade.place_order。
# place 返回后，无论 accepted/rejected/uncertain 都立即执行：
sudo /usr/libexec/hepta-ib-paper-domain-authority \
  --operator-reengage --domain alpha \
  --cycle-id capped-paper-example-v1 \
  --intent-sha256 sha256:<same-64-hex>
```

PAPER session 的 root revoke token 不得放入 `hepta-gw-*` 所有的私有目录；
`hepta-sessionctl` 校验 token inode 的 owner、mode、类型和稳定读取，不检查 parent
ownership；但非 root 调用者无法穿越 `0700 root:root` parent。root operator 必须通过
受审计的 privileged 调用使用
`/run/hepta-session-operator-<domain>` 这类 `0700 root:root` 目录保存 revoke 副本，
Agent 可用副本发布到独立的 `0700 hepta-agent-<domain>` 目录。revoke 后两份 token
都必须 exact-unlink/销毁；若 revoke 不确定，先隔离 Agent 副本并等待 TTL fence。

以下章节保留为 legacy 路径说明。

## 目标
统一前置风控入口，支持：
- 全局 Kill Switch（阻断所有新单）
- Flatten-Only（仅允许减仓/平仓）
- 拒单理由码统一 `RISK_XXX`

## 配置项（IBRisk）
- `GlobalKillSwitch`：`1` 时阻断所有新单
- `FlattenOnly`：`1` 时仅允许减仓单（需要已知持仓）

## 环境变量（高优先级）
- `HEPTA_GLOBAL_KILL_SWITCH=1`
- `HEPTA_FLATTEN_ONLY=1`

## 快速操作
1. 打开全局 Kill Switch（立即阻断）：
   - 设置 `HEPTA_GLOBAL_KILL_SWITCH=1` 后重启进程
2. 切换为 Flatten-Only：
   - 设置 `HEPTA_FLATTEN_ONLY=1` 后重启进程
3. 恢复正常：
   - `HEPTA_GLOBAL_KILL_SWITCH=0`
   - `HEPTA_FLATTEN_ONLY=0`

## 关键理由码
- `RISK_GLOBAL_KILL_SWITCH_ON`
- `RISK_FLATTEN_ONLY_POSITION_UNKNOWN`
- `RISK_FLATTEN_ONLY_BLOCK`
- `RISK_CIRCUIT_BREAKER_TRIPPED`
- `RISK_DUPLICATE_ORDER`

## 验收命令
```powershell
# 1) 编译（按现有工程方式）
msbuild .\HeptaTrader.sln /p:Configuration=Release /p:Platform=x64

# 2) 验证全局 Kill Switch（预期拒单理由包含 RISK_GLOBAL_KILL_SWITCH_ON）
$env:HEPTA_ALLOW_IB_ORDERS="1"
$env:HEPTA_GLOBAL_KILL_SWITCH="1"
$env:HEPTA_IB_TEST_ORDER_LOOP="1"
.\x64\Release\HeptaTrader.exe

# 3) 验证 Flatten-Only（持仓未知时预期 RISK_FLATTEN_ONLY_POSITION_UNKNOWN）
$env:HEPTA_GLOBAL_KILL_SWITCH="0"
$env:HEPTA_FLATTEN_ONLY="1"
.\x64\Release\HeptaTrader.exe
```

## CTP 扩展点
`PreTradeRiskContext` 保留 `venue` / `adapterTag` 字段，可在 CTP 下单适配层复用同一风控引擎，保持理由码统一。
