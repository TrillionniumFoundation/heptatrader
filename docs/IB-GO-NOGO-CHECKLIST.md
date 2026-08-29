# IB 上线 Go/No-Go 清单（当前据实状态）

- 评估时间：2026-02-27 18:xx (Asia/Shanghai)
- 评估范围：`D:\quant\HeptaTrader-master` 当前已有 `docs/` 与 `runtime-logs/` 产物
- 结论口径：仅基于仓库内可追溯证据，不臆测未落盘结果

## 一、检查项（20 项）

| # | 领域 | 检查项 | 状态 | 证据路径 |
|---|---|---|---|---|
| 1 | 构建 | Release 可执行产物存在（可启动前提） | PASS | `x64/Release/HeptaTrader.exe` |
| 2 | 门禁 | CI Gate 白名单检查（WHITELIST）通过 | PASS | `runtime-logs/ci-gate-20260227-181019/ci_gate_summary.json`、`runtime-logs/ci-gate-20260227-181019/whitelist.stdout.log` |
| 3 | 门禁 | CI Gate 回归轮次（IB_REGRESSION_ROUND）通过 | FAIL | `runtime-logs/ci-gate-20260227-181019/ci_gate_summary.json`（`overall=FAIL`, `exitCode=12`） |
| 4 | 门禁 | Release Check 总体可发布 | FAIL | `runtime-logs/release-check-20260227-171603/release_check.json`（`overall=FAIL`） |
| 5 | 门禁 | Release 强制规则文档化（禁止 Skip、要求真实回归+对账） | PASS | `docs/CI-GATE.md` |
| 6 | IB 连通 | IB Healthcheck 连接建立（connectivity） | PASS | `runtime-logs/ib-healthcheck-20260227-075501/summary.json`（`connectivity.pass=true`） |
| 7 | IB 连通 | IB Healthcheck nextValidId 获取成功 | PASS | `runtime-logs/ib-healthcheck-20260227-075501/summary.json`（`nextValidId.pass=true`） |
| 8 | IB 连通 | IB Healthcheck USD/CNH tick 可见 | FAIL | `runtime-logs/ib-healthcheck-20260227-075501/summary.json`（`usdCnhTick.pass=false`） |
| 9 | 回归 | IB paper 下单-撤单闭环（orderCancelLoop）通过 | PASS | `runtime-logs/ib-paper-order-loop/latest/round_report.json`、`runtime-logs/ib-paper-order-loop/latest/order_loop.jsonl` |
|10 | 回归 | IB 回归 latest 轮次报告可用且通过 | PASS | `runtime-logs/ib-regression-round/latest/round_report.json`（`overall=PASS`） |
|11 | 回归 | IB 故障注入回归（disconnect/duplicate/delayed_ack）通过 | PASS | `runtime-logs/ib-fault-regression/20260227-173114/fault_regression_summary.json`（`overall=PASS`） |
|12 | 风控 | 风控开关运行手册（KillSwitch/FlattenOnly）已定义 | PASS | `docs/RUNBOOK-KILLSWITCH.md` |
|13 | 风控 | KillSwitch/FlattenOnly 实测拒单证据（理由码）存在 | BLOCKED | 仅见手册与脚本，未见本轮执行产物（建议补：`runtime-logs/*killswitch*`） |
|14 | 风控 | 订单白名单脚本具备并可执行 | PASS | `scripts/check_ib_order_whitelist.py` + 项 2 通过证据 |
|15 | 对账 | 启动对账规则与处置矩阵（reason_code->action）已固化 | PASS | `docs/RECONCILE-RULES.md` |
|16 | 对账 | 启动对账报告产物存在且无 CRITICAL | FAIL | `runtime-logs/reconcile_startup_report.json` 缺失 |
|17 | 可观测 | 告警基线（P1/P2/P3）已定义 | PASS | `docs/ALERT-RULES-BASELINE.md` |
|18 | 可观测 | 当前轮 alerts.json 已生成并可审计 | BLOCKED | 文档定义了输出，但仓库未见本轮 `alerts.json` 产物 |
|19 | 回归治理 | 回归故障矩阵文档存在 | PASS | `docs/REGRESSION-FAULT-MATRIX.md` |
|20 | 运行手册 | 启动/事故 Runbook 就绪 | PASS | `docs/RUNBOOK-STARTUP.md`、`docs/RUNBOOK-INCIDENT.md` |

---

## 二、关键事实与风险归纳

1. **当前主门禁是失败态**：`ci_gate_summary.json`（18:10）显示 `overall=FAIL`，失败点为 `IB_REGRESSION_ROUND`。
2. **Release Check 最近有效样本为失败态**：`release-check-20260227-171603/release_check.json` 显示 `overall=FAIL`，且明确建议“NOT RELEASABLE”。
3. **IB 健康检查存在行情可用性缺口**：`usdCnhTick.pass=false`，说明连接不等于行情链路完全就绪。
4. **对账闭环证据缺失**：未发现 `runtime-logs/reconcile_startup_report.json`，不满足“启动对账无 CRITICAL”的发布硬条件。
5. **风控/告警文档齐全，但“运行证据”不完整**：KillSwitch 演练、alerts.json 等仍需补充可审计产物。

---

## 三、最终建议

## **No-Go（暂不上线）**

当前不满足 IB 上线最小放行条件，至少存在 2 个硬失败（CI 回归门禁失败、对账报告缺失）+ 1 个关键健康缺口（USD/CNH tick）。

---

## 四、必须完成的前置项（放行前）

1. **重跑并通过 Release Gate（强制）**
   - 命令：`powershell -ExecutionPolicy Bypass -File .\scripts\ci_gate_release.ps1 -ProjectRoot "$PWD"`
   - 要求：`overall=PASS`，并保存最新 `runtime-logs/ci-gate-*/ci_gate_summary.json`。

2. **补齐并通过真实 IB 回归轮次**
   - 目标：`IB_REGRESSION_ROUND.pass=true`，且 `round_report.json` 存在完整 place->status->cancel->final status 闭环。

3. **补齐启动对账报告并验证无 CRITICAL**
   - 目标文件：`runtime-logs/reconcile_startup_report.json`
   - 要求：`startup_action.has_critical=false`。

4. **修复/确认行情链路（USD/CNH tick）**
   - 目标：`ib-healthcheck` 中 `usdCnhTick.pass=true`（或形成经评审批准的“可跳过”豁免记录）。

5. **补充风控与告警的运行证据**
   - 至少补齐：KillSwitch/FlattenOnly 实测日志、`alerts.json` 实际输出样例，落盘于 `runtime-logs/` 并纳入发布包。

---

## 五、建议的复核顺序（执行即验收）

1. `scripts/ib_gateway_healthcheck.ps1`
2. `scripts/run_ib_regression_round.ps1`
3. `scripts/release_check.ps1`
4. `scripts/ci_gate_release.ps1`
5. 启动主程序生成 `reconcile_startup_report.json`，确认无 CRITICAL

> 以上 5 步全部具备“PASS + 产物可追溯”后，再切换本清单结论为 **Go**。
