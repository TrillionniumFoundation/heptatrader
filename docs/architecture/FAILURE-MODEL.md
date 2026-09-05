# 故障模型

Status: current normative
Applies to: all modules, services and external dependencies
Verification: `docs/verification/fault-matrix-v1.json` and fault-injection tests
Authority: failure semantics authority

每个模块必须声明 invalid input、stale/incomplete input、timeout/backpressure、dependency unavailable、state corruption、numeric failure、duplicate/out-of-order event、crash points、restart/fencing 和 resource exhaustion。

- 风险增加：fail closed。
- 读请求：返回 bounded stale/partial reason，不伪造 authoritative。
- 优化器：返回 `NO_FEASIBLE_PLAN`、`STALE_INPUT` 或 `NUMERIC_FAILURE`。
- Execution uncertain：durable uncertain + reconcile，不盲重试。
- 管理面故障：已运行 Execution 保持最小安全自治；禁止自动扩大能力。
- telemetry 故障：不得阻塞交易安全路径，但必须有 dropped/disabled counter。
- emergency exit：使用独立资源和优先级。

故障恢复的目标是重新建立权威真相，而不是恢复某个进程内缓存。
