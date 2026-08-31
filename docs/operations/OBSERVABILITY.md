# 运行时可观测性与 SLO

Status: current normative
Applies to: Gateway, decision, Execution, OMS, state, venue and management
Verification: metric registry, bounded telemetry tests and performance gates
Authority: observability authority

指标围绕交易状态转换，而不是围绕构建脚本。标签必须来自有限词表；account、token、credential、raw order ID、自由模型文本不得成为高基数标签。

必需 counters：tool/session rejection、proposal/plan outcome、risk decision、execution command、journal failure、venue send、execution event、reconcile、state break、kill-switch transition、queue drop/coalesce。

必需 gauges：active sessions/modules/orders、uncertain commands、snapshot/quote age、queue depth、journal/event backlog、connection/recovery state、gross/net exposure、remaining budget。

必需 latency：market event→projection、proposal→plan、snapshot→risk、accepted→journal durable、journal→send、callback→OMS、reconnect→reconcile、emergency request→dispatch。

telemetry 采用 per-thread/per-shard accumulator 和异步 bounded aggregation；采集失败不得阻塞安全路径，但必须暴露 drop/disabled 状态。
