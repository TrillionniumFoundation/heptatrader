# Runtime Observability and SLO

Status: current normative
Applies to: Gateway, strategy/data, Global Decision, Execution, OMS, risk and qualified venues
Verification: metric registry, telemetry tests, performance budgets and incident drills
Authority: runtime observability

Observability 围绕状态迁移而非脚本完成度。metric registry 只允许有限 label；account/token/credential/session/order/prompt/free text 禁止进入 label。

## Required transitions

- market event → authoritative/feature projection；
- proposal receipt → canonical proposal set；
- proposal set → SolverResult/AllocationPlan；
- intent receipt → authoritative snapshot；
- snapshot → risk decision；
- accepted command → journal durable → venue send；
- venue callback → OMS/state projection；
- reconnect → reconcile complete；
- lifecycle change → proposal expiry/quarantine complete。

每条链记录 count、failure reason、queue depth、drop/backpressure 和 latency distribution。Execution correctness SLO 是硬约束：100% accepted mutation journal-before-send、duplicate command 同 outcome、state break 不泄漏到 new-risk、safe exit 不被普通队列饿死。

P1：unjournaled send、journal failure、risk gate bypass、state divergence with gate open、kill switch/fence failure、uncertain exposure timeout。P2：broker disconnected without known exposure、backlog、repeated reject、deadline/latency regression。CI failure是开发阻断，不是生产 P1。
