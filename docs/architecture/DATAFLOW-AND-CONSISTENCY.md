# Dataflow and Consistency

Status: current target contract
Applies to: research, market data, feature, strategy, global decision, state and Execution
Verification: schema, watermark, generation, replay and reconciliation tests
Authority: end-to-end data consistency

## Forward decision flow

```text
point-in-time input
-> normalized market event
-> feature snapshot
-> StrategyProposal
-> canonical proposal set
-> SolverResult + AllocationPlan
-> target-position intent
-> deterministic risk + permit
-> durable command
-> venue send
```

## Reverse authority flow

```text
venue callback/broker snapshot
-> adapter normalization
-> OMS journal/event ordering
-> order/position/account projection
-> reconciliation
-> authoritative snapshot generation
-> proposal/plan/intent consumers
```

研究和模型数据只沿 forward flow 提供观点；Broker/Execution 只沿 reverse authority flow 提供事实。Agent 或策略不能把缓存仓位、客户端时间或模型文本注入 authoritative snapshot。

## Consistency envelope

每个跨模块对象绑定 producer、epoch/fence、shard、sequence/watermark、observed/captured time、fresh-until、schema version、numeric policy 和 digest。SnapshotVector 指定所有 required shard generation；同一决策不得拼接未声明的不同 generation。

## Recovery

重启从 durable journal 和 authoritative venue snapshot 恢复。未知 send outcome 进入 `uncertain`，复用原 command ID，通过 query/reconcile 解决，不产生新 order。duplicate/out-of-order/correction event 按 event identity 和 venue lifecycle 合并；无法收敛时关闭 new-risk gate并 terminal latch。
