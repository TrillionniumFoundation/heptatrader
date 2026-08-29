# IB Gateway USD/CNH Paper 最小下单闭环（安全版）

目标：`placeOrder -> orderStatus -> cancelOrder -> final status`

## 安全约束

- 默认只读：`IBServer ReadOnly="1"` 或不设置 `HEPTA_ALLOW_IB_ORDERS`
- 只有显式设置 `HEPTA_ALLOW_IB_ORDERS=1` 才允许下单
- 测试闭环必须显式设置 `HEPTA_IB_TEST_ORDER_LOOP=1`
- 默认风控：`HEPTA_IB_MAX_ORDER_QTY=1000`、`HEPTA_IB_MAX_DAILY_ORDERS=1`
- 新增保护：价格偏离保护、重复下单抑制、错误码黑名单熔断（见 `IBRisk`）
- 账户白名单默认 `DU*`（仅 Paper），非白名单账户启动硬失败
- 实盘硬保护：默认 `AllowLiveTrading=0` + `LiveKillSwitch=1`，未显式授权不能实盘下单
- 建议仅连接 Paper Gateway（通常端口 4002 / 7497-paper）

## Paper soak 前置门禁（必做）

在开始 paper soak 前，先跑本地最小 CI 门禁：

```powershell
powershell -ExecutionPolicy Bypass -File .\gate-local.ps1
```

- 退出码 `0`：可以继续 paper soak。
- 非 `0`：查看 `runtime-logs/ci-gate-*/ci_gate_summary.txt`，修复后重试。
- 详细规范见 `docs/CI-GATE.md`。

## 一键复现

1. 确保 IB Gateway/TWS 已登录 **Paper** 账户，API 开启，允许 localhost。
2. 在仓库根目录执行：

```powershell
# 1) 安全验证（不允许下单，预期被拦截）
powershell -ExecutionPolicy Bypass -File .\scripts\run_ib_paper_loop.ps1 -Port 4002

# 2) 显式放开后执行闭环
powershell -ExecutionPolicy Bypass -File .\scripts\run_ib_paper_loop.ps1 -Port 4002 -AllowOrders
```

3. 日志在 `runtime-logs/ib-paper-loop-*.out.log`。

## 关键日志判定

成功闭环至少应出现：

- `[IB-TEST] placeOrder sent orderId=...`
- `[IB] orderStatus id=... status=...`
- `[IB-TEST] cancelOrder sent orderId=...`
- `[IB-TEST] final status reached ... status=Cancelled|ApiCancelled|Inactive`
- `[IB-TEST] order loop completed: place -> status -> cancel -> final status`

只读/未授权模式应出现：

- `IB ReadOnly=0 blocked. Set HEPTA_ALLOW_IB_ORDERS=1 ...`
  或
- `[IB-TEST] placeOrder failed (gate/validation/API)...`

## 统一回归入口（推荐）

现在可直接使用统一入口脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_ib_regression_round.ps1 -Port 4002
```

每次执行会在 `runtime-logs/ib-regression-round/<roundId>/` 产出：

- `round_report.json`（机器可读）
- `round_report.txt`（一行一项，便于 CI/grep）
- `round_report.md`（人工阅读）
- `order_loop.jsonl`（事件明细）

报告固定包含并判定 PASS/FAIL：

- 连接（connectivity）
- `nextValidId`
- USD/CNH tick
- 下单/撤单闭环（place -> cancel -> final status）
- Overall（汇总 PASS/FAIL）

## IBRisk 新字段（建议）

```xml
<IBRisk
  MaxPriceDeviationBps="30"
  DuplicateOrderWindowSec="3"
  DuplicatePriceTolerance="0.0001"
  EnableErrorCodeBlacklist="1"
  ErrorCodeBlacklist="201,202,10147,10148"
  AccountWhitelist="DU*"
  AllowLiveTrading="0"
  LiveKillSwitch="1" />
```

说明：
- `MaxPriceDeviationBps`：LMT 价格相对最新 tick 的最大偏离（bps），超过拒单。
- `DuplicateOrderWindowSec` + `DuplicatePriceTolerance`：窗口内相同合约/方向/类型/数量/近似价格视作重复单，拒绝再次下发。
- `ErrorCodeBlacklist`：命中黑名单错误码立即熔断，关闭下单闸门。
- `AccountWhitelist`：账户白名单（支持前缀通配，如 `DU*`）。
- `AllowLiveTrading`：实盘显式授权开关（默认 `0`）。
- `LiveKillSwitch`：实盘 kill switch（默认 `1`，开启即阻断实盘）。

环境变量覆盖：
- `HEPTA_IB_ACCOUNT_WHITELIST`
- `HEPTA_ALLOW_IB_LIVE`
- `HEPTA_IB_LIVE_KILL_SWITCH`

## 快速提取证据

```powershell
Get-ChildItem .\runtime-logs\ib-regression-round\latest\*.txt |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 |
  % { Get-Content -Path $_.FullName }
```

## 结构化可观测性与时延统计

新增适配器 JSONL 关键事件日志：`runtime-logs/ib_observability.jsonl`
（可用环境变量 `HEPTA_IB_OBS_LOG` 覆盖路径）。

当前覆盖路径：
- connect：`connect.start` / `connect.connected_event` / `connect.next_valid_id` / `latency(path=connect, stage=api_connect)`
- order：`latency(path=order, stage=api_place)`、`latency(path=order, stage=submit_to_first_status)`
- cancel：`latency(path=cancel, stage=api_cancel)`、`latency(path=cancel, stage=submit_to_final_cancel)`

P50/P95 汇总脚本：

```powershell
python .\scripts\ib_obs_latency_stats.py --input .\runtime-logs\ib_observability.jsonl
# 生成 Markdown 报告
python .\scripts\ib_obs_latency_stats.py --input .\runtime-logs\ib_observability.jsonl --output .\runtime-logs\ib-latency-summary.md
```
