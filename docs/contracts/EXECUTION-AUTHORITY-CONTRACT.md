# Execution Authority V1

Status: current normative
Applies to: Execution, OMS, state, risk, reconcile and venue adapters
Verification: journal, idempotency, fencing, replay and venue boundary tests
Authority: venue mutation authority

Execution Authority 对每次 mutation 执行：

```text
identity/session/capability
  -> typed schema and bounds
  -> epoch/fence/generation
  -> deterministic final risk
  -> command fingerprint
  -> durable journal
  -> venue send
  -> callback projection
  -> reconciliation
```

Adapter 只负责 transport/session translation、request/response mapping、event normalization、venue-specific stricter validation 和 observed lifecycle。它不负责 strategy、capital allocation、最终组合风险或 capability promotion。未实现 transport 必须 typed fail closed。

crash、timeout 或 lost response 形成 durable uncertain state。系统查询 command status、Broker open orders/positions/executions 并对账；不得生成新 command ID 盲重发。
