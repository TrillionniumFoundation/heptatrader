# Dataflow and Consistency

Status: current target contract
Applies to: research, market data, feature, strategy, global decision, state and Execution
Verification: schema, watermark, generation, coherent-cut, replay and reconciliation tests
Authority: end-to-end data consistency

## Forward decision flow

```text
point-in-time input
-> normalized market event
-> verified market snapshot / coherent snapshot vector
-> feature snapshot
-> StrategyProposal
-> canonical ProposalSet with bounded lifetime
-> SolverResult + sealed AllocationPlan receipt
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
-> authoritative snapshot generation and validity ceiling
-> proposal/plan/intent consumers
```

研究和模型数据只沿 forward flow 提供观点；Broker/Execution 只沿 reverse authority flow 提供事实。Agent 或策略不能把缓存仓位、客户端时间或模型文本注入 authoritative snapshot。

## Market snapshot verification

`MarketDataSnapshot` 是可复制的数据结构，不因为类型名称而自动成为可信 capability。任何 feature、risk 或决策消费者在使用前必须：

1. 检查 found、epoch、sequence、generation、时间字段与 fixed numeric invariant；
2. 重建对应 `MarketDataEvent`；
3. 重新执行 bid/ask、size、identity 和时间验证；
4. 重算 canonical event digest，再把该 digest、store generation 与 sequence-gap 状态绑定成 snapshot-level digest，并精确比较。

伪造 digest、digest 后字段修改、反向 quote、越界 raw、sequence gap、stale 或 clock regression 全部 fail closed。

多 key `ReadVector` 必须先规范化并去重 key，再按 canonical shard ID 顺序持有全部参与 shard 的锁，在完整锁集合保持期间读取所有组件并生成 vector digest。这样得到的是一个 coherent in-memory lock cut；它不宣称全系统具有未实现的全局 MVCC generation。writer 只能在该 cut 完成后推进任一参与 shard，且 barrier-controlled interleaving test 必须证明这一点。

## Decision lifetime and provenance

每个跨模块对象绑定 producer、epoch/fence、shard、sequence/watermark、observed/captured time、fresh-until、schema version、numeric policy 和 digest。ProposalSet 还绑定 expected-module completeness 与全部 proposal/snapshot validity 的交集。AllocationPlan 不能延长该交集，并绑定 allocator epoch、book/pool、policy revision 与 authoritative snapshot identity。

当前同进程 shadow 路径只接受 `GlobalAllocator` 创建的 construction-restricted receipt，并在 Execution 重新匹配当前 context、时间窗口、solver evidence、fixed targets 与 PortfolioCompiler 结果。该 receipt 不是跨进程认证；任何远程或 PAPER plan intake 在 authenticated authority envelope 定义和验证前保持关闭。

## Recovery

重启从 durable journal 和 authoritative venue snapshot 恢复。未知 send outcome 进入 `uncertain`，复用原 command ID，通过 query/reconcile 解决，不产生新 order。duplicate/out-of-order/correction event 按 event identity 和 venue lifecycle 合并；无法收敛时关闭 new-risk gate 并 terminal latch。
