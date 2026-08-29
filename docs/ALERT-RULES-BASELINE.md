# Alert Rules Baseline (W11 / 阶段F)

## 1. 严重级别

- **P1（立即处理）**：可能导致无法交易、风险失控、状态不一致。
- **P2（尽快处理）**：交易能力下降或出现异常趋势。
- **P3（观察）**：暂不影响交易，仅需跟踪。

## 2. 最小告警规则

### P1
1. `ci_gate_overall != PASS`
   - 动作：阻断合并/发布；查看 `ci_gate_summary.json`。
2. `ib_next_valid_id_count == 0`
   - 动作：判定 IB 会话未建立；执行 `RUNBOOK-INCIDENT` 的“连接失败”流程。
3. `ib_error_code_201 > 0`（示例：下单拒绝）
   - 动作：暂停策略下单，核查合约与权限。

### P2
1. `ib_tick_price_count == 0`（在应有行情订阅前提下）
2. `ib_error_total` 在单轮回归明显上升（与近 7 日均值比较）

### P3
1. 单个非关键错误码偶发（可恢复）

## 3. 告警输出格式（本仓库最小实现）

`summarize_ib_logs.ps1` 会生成 `alerts.json`：

```json
[
  {
    "severity": "P1",
    "rule": "NO_NEXT_VALID_ID",
    "message": "No nextValidId detected",
    "value": 0
  }
]
```

## 4. 升级路径

- V1：本地 JSON 告警 + 人工查看
- V2：接入 CI Job Summary/通知
- V3：接入 Prometheus + Alertmanager + 值班轮值
