# RECONCILE-RULES

## 启动对账（闭环）
启动时生成 `runtime-logs/reconcile_startup_report.json`（可通过 `HEPTA_RECONCILE_REPORT_PATH` 覆盖），并基于 **Broker 启动快照 + OMS journal replay** 做一致性检查。

## 处置动作定义
- `block`：阻断启动，主程序按 `startup_action.block_exit_code` 退出
- `warn`：允许继续启动，但打印告警日志，要求值班关注
- `manual`：允许继续启动，但要求人工处置（数据缺失/脏数据）
- `auto-fix`：无需人工动作（通过或可自动消化）

## 输入模型
1. **Broker open orders**：`HEPTA_BROKER_OPEN_ORDERS_PATH`（默认 `runtime-logs/broker_open_orders.csv`）
2. **Broker positions**：`HEPTA_BROKER_POSITIONS_PATH`（默认 `runtime-logs/broker_positions.csv`）
3. **Broker cash**：`HEPTA_BROKER_CASH_PATH`（默认 `runtime-logs/broker_cash.txt`）
4. **OMS replay**：`HEPTA_OMS_JOURNAL_PATH`（默认 `runtime-logs/oms_journal.jsonl`）
5. **可选 OMS 现金锚点**：`HEPTA_OMS_REPLAY_CASH`

## 输出模型
报告内 `checks[]` 每条均包含：
- `name`
- `severity`：`INFO` / `WARN` / `CRITICAL`
- `reason_code`
- `action`：`block/warn/auto-fix/manual`
- `detail`

并输出：
- `startup_action.decision`
- `startup_action.has_critical`
- `startup_action.block_exit_code`

`status` 字段为报告整体严重级别（取 checks 最大严重级别）。

## reason_code -> action 处置矩阵（v3）
| reason_code | severity | action | 说明 |
|---|---|---|---|
| RISK_RECON_OPEN_ORDER_MISMATCH | CRITICAL | block | 挂单不一致，可能重复/漏单风险 |
| RISK_RECON_POSITION_MISMATCH | CRITICAL | block | 持仓不一致，风险敞口不可信 |
| RISK_RECON_CASH_MISMATCH | WARN | warn | 现金偏差，先告警后继续 |
| RISK_RECON_CASH_UNAVAILABLE | INFO | warn | 现金锚点不可比对，降级告警 |
| RISK_RECON_BROKER_OPEN_ORDERS_MISSING | WARN | manual | broker 挂单快照缺失，需人工补数据 |
| RISK_RECON_BROKER_POSITIONS_MISSING | WARN | manual | broker 持仓快照缺失，需人工补数据 |
| RISK_RECON_BROKER_CASH_MISSING | WARN | manual | broker 现金快照缺失，需人工补数据 |
| RISK_RECON_BROKER_CASH_EMPTY | WARN | manual | broker 现金文件为空，需人工修复 |
| RISK_RECON_BROKER_OPEN_ORDERS_BAD_LINE | WARN | manual | 挂单 CSV 脏行，需人工修复 |
| RISK_RECON_BROKER_POSITIONS_BAD_LINE | WARN | manual | 持仓 CSV 脏行，需人工修复 |
| RISK_RECON_ORDERS_MATCH | INFO | auto-fix | 挂单一致，自动通过 |
| RISK_RECON_POSITIONS_MATCH | INFO | auto-fix | 持仓一致，自动通过 |
| RISK_RECON_CASH_MATCH | INFO | auto-fix | 现金一致，自动通过 |
| RISK_RECON_OMS_REPLAY_SUMMARY | INFO | auto-fix | replay 摘要信息，不需人工动作 |

> 至少 8 个 reason_code 已固化（当前共 14 个）。

## 启动流程策略
1. ReconcileEngine 对每条 check 输出 `action`。
2. 汇总得到 `startup_action.decision`（优先级：`block > manual > warn > auto-fix`）。
3. HeptaDemoStrategyTrader 执行：
   - `block`：阻断启动并退出（默认 `-16`，可由 `HEPTA_RECONCILE_BLOCK_EXIT_CODE` 覆盖）
   - `warn/manual`：打印 `[RECONCILE-warn]` / `[RECONCILE-manual]` 明细并继续
   - `auto-fix`：正常继续

## 示例片段
```json
{
  "startup_action": {
    "decision": "manual",
    "has_critical": false,
    "block_exit_code": 0
  },
  "checks": [
    {
      "name": "broker_cash_input",
      "severity": "WARN",
      "reason_code": "RISK_RECON_BROKER_CASH_MISSING",
      "action": "manual",
      "detail": "missing=runtime-logs/broker_cash.txt"
    }
  ]
}
```

---

## Smoke 验证
### 1) 运行脚本
```powershell
python scripts/reconcile_startup_smoke.py --workdir .
```

### 2) 脚本验证内容
- 生成 `runtime-logs/reconcile-fixture/` 的 match/mismatch/missing-input 三类 fixture
- 按处置矩阵校验 reason_code -> action 映射
- 校验启动决策：
  - match => `startup_action.decision=auto-fix`
  - mismatch => `startup_action.decision=block`
  - missing-input => `startup_action.decision=manual`

### 3) 接入主程序（有可执行文件时）
```powershell
$env:HEPTA_OMS_JOURNAL_PATH="runtime-logs/reconcile-fixture/oms_journal_match.jsonl"
$env:HEPTA_BROKER_OPEN_ORDERS_PATH="runtime-logs/reconcile-fixture/broker_open_orders_match.csv"
$env:HEPTA_BROKER_POSITIONS_PATH="runtime-logs/reconcile-fixture/broker_positions_match.csv"
$env:HEPTA_BROKER_CASH_PATH="runtime-logs/reconcile-fixture/broker_cash_match.txt"
$env:HEPTA_OMS_REPLAY_CASH="100000"

# 根据你的构建产物路径调整
.\x64\Release\HeptaTrader.exe
Get-Content runtime-logs/reconcile_startup_report.json
```


## 运行时 Shadow Reconcile（IB）验证命令
```powershell
$env:HEPTA_SHADOW_RECON_INTERVAL_SEC="5"   # 默认120，验证时缩短

# 启动程序（按你的产物路径调整）
.\x64\Release\HeptaTrader.exe

# 另开窗口观察 CRITICAL 漂移日志与 OMS 事件
Get-Content runtime-logs/HeptaTrader.log -Wait | Select-String "SHADOW-RECONCILE|CRITICAL"
Get-Content runtime-logs/oms_journal.jsonl -Wait | Select-String '"event_type":"reconcile_drift"|"eventType":"reconcile_drift"'
```

预期：当 IB Position 累计仓位与本地预期仓位不一致时，日志出现 `CRITICAL reconcile_drift`，并写入 `reconcile_drift` OMS 事件，同时交易通道降级为 `flattenOnly=1`。
