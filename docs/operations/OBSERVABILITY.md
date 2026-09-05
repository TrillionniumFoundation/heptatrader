# Runtime Observability and SLO

Status: current normative
Applies to: Gateway, strategy/data, Global Decision, Execution, OMS, risk and qualified venues
Verification: metric registry, source-emission drift test, telemetry tests, performance budgets and incident drills
Authority: runtime observability

Observability 围绕状态迁移而非脚本完成度。`metric-registry-v1.json` 是 metric name/type/unit/owner/label 的机器权威；`tests/python/test_metric_registry.py` 扫描生产 C/C++ 中的 literal emission，任何未注册 metric、类型漂移、bucket 漂移或 series-cap 漂移都会阻断 CI。

## Privacy and cardinality boundary

- 只允许注册表中的有限 label name；未知 label name/value 归一为 `redacted`；
- account、account ID、token、credential、session/order identity、prompt 和 free text 禁止成为 label；
- reason code、tool、event type、status、operation、outcome、state、environment 和 venue 使用 finite vocabulary；
- process-local series 总上限为 2048，超限只增加 `dropped_series`，不允许 unbounded map；
- telemetry 不拥有交易 authority，导出故障不能授权重试或阻断已证明安全的 cancel/reduce/flatten。

## Implemented metric contract

| Metric | Type / unit | Owner | Labels | Operational meaning |
|---|---|---|---|---|
| `hepta_tool_calls_total` | counter / calls | Gateway | tool, status, reason_code | typed tool ingress outcomes |
| `hepta_session_rejections_total` | counter / rejections | Session | reason_code | peer/session/capability denial |
| `hepta_risk_decisions_total` | counter / decisions | Risk | decision, reason_code | deterministic allow/reject |
| `hepta_risk_decision_latency_microseconds` | histogram / µs | Risk | none | exact fixed-point evaluation latency |
| `hepta_execution_events_total` | counter / events | Execution | event_type | canonical execution lifecycle events |
| `hepta_venue_sends_total` | counter / attempts | Execution | operation, status | place/cancel/flatten send attempts |
| `hepta_execution_commands_total` | counter / commands | Execution | status, reason_code | command lifecycle outcomes |
| `hepta_oms_journal_failures_total` | counter / failures | Execution | reason_code | append/sync/replay failure |
| `hepta_reconcile_runs_total` | counter / runs | Execution | operation, outcome, reason_code | reconciliation convergence |
| `hepta_state_breaks_total` | counter / breaks | Execution | kind | projection/state break requiring gate response |
| `hepta_kill_switch_transitions_total` | counter / transitions | Execution | state | observed kill-switch changes |

`hepta_snapshot_age_ms` 已注册为 declared gauge，但在生产 emission 被实现并通过 source-registry drift test 之前不能用于 readiness 或 SLO claim。

所有 latency histogram 的 runtime bucket 单位为 microseconds，边界固定为：`10, 50, 100, 500, 1000, 5000, 10000, 50000, 100000, 500000, 1000000, 5000000`。名字带 `_microseconds` 的 metric 不得在 registry 声明为 milliseconds。

## Required transition coverage

下列链必须通过已实现 metric、authoritative log/journal 或后续已注册 metric 覆盖；缺少 telemetry 不能被解释为成功：

- market event → authoritative/feature projection；
- proposal receipt → canonical proposal set；
- proposal set → SolverResult/AllocationPlan；
- intent receipt → authoritative snapshot；
- snapshot → risk decision；
- accepted command → journal durable → venue send；
- venue callback → OMS/state projection；
- reconnect → reconcile complete；
- lifecycle change → proposal expiry/quarantine complete。

当前 registry 只把真实 production emission 标记为 `implemented`。尚未接线的 market/feature/proposal/allocator/lifecycle queue、drop 和 latency metric 必须以明确 `declared` 状态加入，随后在同一 revision 实现 source emission、bounded labels 和 tests；文档不得提前把它们描述为可用 SLO。

## Correctness SLO and alert classes

Execution correctness 是硬约束，不以百分比预算放宽：

- 100% accepted mutation journal-before-send；
- duplicate command ID + same payload 返回同一 durable outcome；
- state break、journal failure、fence uncertainty 或 reconcile divergence 时 new-risk gate 必须关闭；
- kill switch engagement 和 strict safe-exit 不被普通队列饿死；
- account/credential/opaque identity 不进入 metric key 或 snapshot。

### P1 — immediate incident and gate closure

- `hepta_oms_journal_failures_total` 增量；
- unjournaled send（由 journal/send assertion evidence 检测）；
- `hepta_state_breaks_total` 增量且 new-risk gate 未关闭；
- kill-switch/fence 失效；
- uncertain exposure 超过 policy deadline；
- telemetry 发现 secret/account label（测试和 exporter gate 必须拒绝）。

关联 runbook：`INCIDENT-RESPONSE.md`、`KILL-SWITCH.md`、`RECONCILIATION.md`。

### P2 — degraded service, no silent risk expansion

- broker disconnected but known exposure remains bounded；
- command/reconcile backlog 或 repeated reject；
- risk/transition latency 超过 reviewed performance budget；
- series-cap drops 持续发生；
- declared snapshot freshness metric 不可用时 readiness 不能依赖它。

CI failure 是开发阻断，不是生产 P1。Dashboard、exporter 和 alert rule 只能消费 registry metric；自由文本解析不能成为唯一安全告警来源。

## Evidence and change control

Metric 变更必须同时修改 registry、production emission、finite label vocabulary、C++ telemetry tests、source-registry drift test、相关 SLO/alert 和 runbook。删除或重命名 metric 是 compatibility change；不能通过 dashboard alias 隐藏生产契约漂移。Raw metric snapshot 是诊断数据，除非其他 authority contract 明确指定，否则不是交易状态或 qualification receipt。
