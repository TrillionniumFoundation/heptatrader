# Regression Fault Matrix (P0-4)

本矩阵定义 IB 回归中的三类故障注入场景、执行入口与 pass/fail 判定规则。

## 执行入口

- 单场景脚本：
  - `scripts/fault_injection_disconnect.ps1`
  - `scripts/fault_injection_duplicate_callbacks.ps1`
  - `scripts/fault_injection_delayed_ack.ps1`
- 聚合执行：`scripts/run_ib_fault_regression.ps1`
- 主回归接入：`scripts/run_ib_regression_round.ps1`（默认执行故障注入，可通过 `-SkipFaultInjection` 跳过）

## 场景矩阵

| 场景 | 注入方式（当前） | 关键观测点 | PASS 规则 | FAIL 规则 |
|---|---|---|---|---|
| Disconnect / Reconnect | 可复现模拟日志回放（`transport_disconnected`→`reconnected`） | 断连事件、重连事件、重连时延、订单终态 | 同时满足：1) 存在断连事件；2) 存在重连事件；3) `reconnect_delay_sec <= reconnectWithinSec`（默认 5s）；4) 最终订单状态为 `Cancelled` | 任一条件不满足即 FAIL |
| Duplicate Callbacks | 可复现模拟日志回放（重复 `order_status` 回调） | 去重后状态序列、重复计数、最终状态 | 同时满足：1) 去重序列稳定且等于 `Submitted -> PreSubmitted -> Cancelled`；2) `duplicate_count >= minDuplicateCount`（默认 2）；3) 最终状态 `Cancelled` | 任一条件不满足即 FAIL |
| Delayed ACK | 可复现模拟日志回放（提交后 ACK 延迟） | ACK 是否出现、ACK 延迟、最终状态 | 同时满足：1) 出现 `order_ack_received`；2) `ack_delay_sec >= minExpectedDelaySec`（默认 5s）；3) `ack_delay_sec <= maxAllowedDelaySec`（默认 30s）；4) 最终状态 `Cancelled` | 任一条件不满足即 FAIL |

## 聚合判定

`run_ib_fault_regression.ps1` 对三个场景逐一执行：

- 单场景 exit code = 0 记为 PASS；非 0 记为 FAIL。
- 聚合总体规则：
  - 所有场景 PASS => `OVERALL=PASS`（exit 0）
  - 任一场景 FAIL => `OVERALL=FAIL`（exit 1）

## 与主回归的关系

`run_ib_regression_round.ps1` 的 `OVERALL` 由两部分共同决定：

- `IB_ORDER_LOOP`（原有下单/撤单回归）
- `FAULT_INJECTION`（本故障注入聚合）

最终规则：

- `IB_ORDER_LOOP=PASS` 且 `FAULT_INJECTION=PASS`（或显式 `-SkipFaultInjection`）=> `OVERALL=PASS`
- 否则 `OVERALL=FAIL`

## 产物路径

- 故障聚合运行目录：`runtime-logs/ib-fault-regression/<roundId>/`
- 聚合摘要：`fault_regression_summary.json` / `fault_regression_summary.txt`
- 单场景产物：各场景子目录中的 `*.jsonl`、`*_report.json`、`*_report.txt`

---

## CTP 回归包（两周冲刺 #2）

执行入口：`scripts/run_ctp_regression_round.ps1`

该脚本与 `run_ib_regression_round.ps1` 保持一致的产物与退出语义：

- 运行目录：`runtime-logs/ctp-regression-round/<roundId>/`
- 输出：`round_report.json` / `round_report.txt` / `round_report.md`
- 链接目录：`runtime-logs/ctp-regression-round/latest/`
- 退出码：`OVERALL=PASS` 返回 0，否则返回 1

### CTP 基础场景覆盖

| 场景 | 注入/来源 | 关键观测点 | PASS 规则 |
|---|---|---|---|
| 下单/撤单 | 模拟日志回放（`order_intent -> place_sent -> cancel -> order_status(cancelled)`） | 下单、撤单、终态 | 事件链完整且终态 `cancelled` |
| 拒单 | 模拟日志回放（`reject`） | 拒单事件、拒因 | 存在拒单事件且具备 `reason` |
| 断线重连 | 模拟日志回放（`transport_disconnected -> reconnected`） | 断连事件、重连事件、重连时延 | 同时存在断连与重连，且 `reconnect_delay_sec <= reconnectWithinSec` |
| 现网开关（可选严格） | 读取既有开关：`HEPTA_CTP_TEST_ORDER_LOOP`、`HEPTA_ALLOW_CTP_ORDERS` | 开关状态记录 | 默认仅记录；`-StrictEnvSwitch` 下必须均为 `1` |

### 验证命令

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_ctp_regression_round.ps1 -ProjectRoot D:\quant\HeptaTrader-master
```

可选严格模式（校验现有开关）：

```powershell
$env:HEPTA_CTP_TEST_ORDER_LOOP='1'
$env:HEPTA_ALLOW_CTP_ORDERS='1'
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_ctp_regression_round.ps1 -ProjectRoot D:\quant\HeptaTrader-master -StrictEnvSwitch
```
