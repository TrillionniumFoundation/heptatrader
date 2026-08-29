# Observability Metrics Baseline (W11 / 阶段F)

本文定义当前版本最小可落地指标，优先服务 paper/准生产阶段排障与门禁。

## 1. 指标范围（最小集）

| 指标 | 来源 | 含义 | 建议阈值 |
|---|---|---|---|
| `ib_next_valid_id_count` | `summarize_ib_logs.ps1` | 会话中 nextValidId 出现次数 | `>=1` |
| `ib_tick_price_count` | `summarize_ib_logs.ps1` | 行情 tickPrice 事件数量 | `>=1`（有订阅时） |
| `ib_error_total` | `summarize_ib_logs.ps1` | 错误总数 | 关注趋势 |
| `ib_error_code_<code>` | `summarize_ib_logs.ps1` | 按错误码聚合 | 关键错误触发告警 |
| `ci_gate_overall` | `scripts/ci_gate.ps1` | 门禁结果（PASS/FAIL） | 必须 PASS |
| `ci_gate_exit_code` | `scripts/ci_gate.ps1` | 失败分层退出码 | 0 才允许进入 soak/live 准备 |

> 说明：当前为日志派生指标（log-derived metrics），后续可升级为 Prometheus exporter。

## 2. 产物与路径

- CI 门禁：`runtime-logs/ci-gate-*/ci_gate_summary.json`
- IB 日志汇总：`runtime-logs/ib-log-summary-*/summary.md`
- 机器可读汇总（新增）：`runtime-logs/ib-log-summary-*/summary.json`
- 告警判定（新增）：`runtime-logs/ib-log-summary-*/alerts.json`

## 3. 采集建议

1. 每次执行 `run_ib_regression_round.ps1` 后调用 `summarize_ib_logs.ps1`。
2. 每次夜间 soak 结束后归档 `summary.json + alerts.json`。
3. 将最近 7 天 error code 直方图做对比，重点观察 1100/1101/1102/201 类连接/下单异常。

## 4. 与运行手册联动

- 启动流程：见 `RUNBOOK-STARTUP.md`
- 事故处置：见 `RUNBOOK-INCIDENT.md`
- 上线清单：见 `PROD-GO-LIVE-CHECKLIST.md`
