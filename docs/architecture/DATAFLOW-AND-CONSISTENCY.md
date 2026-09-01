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

## Snapshot integrity and coherent vector cuts

Before feature or decision consumption, a market-data snapshot is reconstructed as its canonical event, every bounded fixed and timing field is validated, the event digest is recomputed, and any mismatch is rejected.

A multi-instrument vector is one coherent store cut. The reader sorts the target shard set, acquires every target shard lock in canonical order, reads and validates all components while those locks remain held, and only then computes the vector digest. Writers cannot advance one component between vector reads. Duplicate keys, missing components, sequence gaps, stale values, clock regression and digest failure are fail-closed.

## Same-process market authority and currentness

Risk-ready Market Data authority is not a freely transferable snapshot wrapper. A receipt is construction-restricted and bound to one exact `ShardedMarketDataStore` handle, process-local issuer identity, issuer lifecycle, source key/epoch/sequence/generation/digest and issuance time. The authoritative store owns the clock used for issuance and use-time freshness checks; caller-provided time is diagnostic only.

Feature is constructed against one exact Market Data authority. Before every authoritative Feature write, the issuer revalidates the receipt against its current entry. Before every risk-ready Feature read, the source lineage is revalidated again. Cross-store transfer, reconstructed-store substitution, issuer destruction, generation advance, gap-after-issuance, clock regression, expiry and replay into a fresh Feature store fail closed. A future cross-process deployment requires a separately authenticated issuer/audience/nonce/expiry envelope; the same-process receipt is never serialized as proof.
