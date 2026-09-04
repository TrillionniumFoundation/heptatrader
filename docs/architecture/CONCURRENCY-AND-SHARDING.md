# 并发、分片与 Backpressure

Status: current contract for the named capability transaction; target contract for other scheduling behavior
Applies to: data plane, strategies, Global Decision, state, OMS, Gateway and Execution
Verification: deterministic scheduler, ordering, overload, fairness and emergency-lane tests
Authority: concurrency semantics

## Ownership model

- execution domain、capital pool、market-data shard 和 module instance 均有唯一 active writer/leader；
- readers 使用 immutable generation snapshot、RCU/double-buffer 或明确 actor message；
- 跨模块共享 mutable pointer、全局 map 和无 owner mutex 禁止；
- module internal lock 不跨 network/Broker/filesystem I/O；跨 module call 默认禁止，唯一当前例外是下述 Market Data / Feature capability transaction；
- leader change 增加 epoch/fence，旧 writer 的输出被拒绝。

## Current Market Data / Feature capability transaction

This is a description of the current same-process implementation, not a new permission to execute arbitrary callbacks under a lock. It is the only permitted `cross_module_lock` exception: `marketdata-feature-capability-transaction-only`, declared by `hepta.marketdata.runtime` and `hepta.feature.runtime`. All other modules retain `forbidden`. These declarations must agree with their generated technical guides.

The current lock order is:

```text
MarketDataAuthorityState::mutex (lifecycle / issuer / authority clock)
  -> source ShardedMarketDataStore shard mutex
    -> ShardedFeatureStore shard mutex (commit)
```

`MarketDataConsumerBinding::WithCurrentReceipt` and `ShardedMarketDataStore::UseReceiptLocked` retain the lifecycle and source-shard locks while the private `ShardedFeatureStore::Compute` consumer validates, constructs and commits the derived snapshot. Sampling authority time happens after acquiring the source shard. Currentness validation and Feature commit share one linearization interval: an issuer fence, source generation change or sequence gap cannot slip between validation and the accepted write. Releasing the source lock before committing is not an admissible latency optimization. The full receipt rules remain in [Dataflow and Consistency](DATAFLOW-AND-CONSISTENCY.md).

The inverse order is forbidden. `ShardedFeatureStore::GetRiskReady` obtains a copy through `Get`, releases the Feature shard lock, and only then calls `MarketDataConsumerBinding::ResolveLineage`. A Feature lock must not be retained while acquiring the lifecycle or source-shard lock. Future code must preserve that order, including failure and shutdown paths.

The consumer is private trusted derivation code, not a strategy/plugin extension point. It must not perform Broker, network or filesystem I/O, recursively enter Market Data authority, wait for another worker, or call an untrusted strategy. The injected authority clock is trusted and non-reentrant. Test hooks may coordinate deterministic tests but are not a production extension or a production latency guarantee.

The current implementation performs checked arithmetic, string/digest construction and bounded map updates inside this transaction. A single lifecycle mutex serializes these receipt operations for one authority even when keys map to different source shards. Therefore shard count does not prove parallel scaling, bounded lock wait, a hard real-time deadline, or target-host throughput qualification.

Any subsequent optimization must preserve use-time freshness, issuer lifetime, fencing and generation checks; use deterministic stale-during-wait and source-update-versus-commit tests; and separately measure lock wait, critical-section duration and p99/p99.9 latency on the intended host. No such deployment qualification is granted by documenting this exception. Registry/guide agreement tests verify declarations only, not deadlock freedom or performance.

## Shard keys

| Domain | Primary shard | Ordering authority |
|---|---|---|
| Market data | venue + instrument | feed epoch + sequence |
| Feature | instrument + feature set | input watermark + feature generation |
| Strategy | module instance + account book | proposal sequence |
| Global allocation | capital pool + account/risk book | allocator epoch + plan sequence |
| State/OMS/Execution | execution domain | execution epoch + journal/event sequence |

跨 shard 读取使用 snapshot vector，不假装存在全局瞬时同时点。vector 中任一 required component 过期或改变，风险增加决策拒绝。

## Queue classes

- market/feature：bounded latest-value/coalescing，丢弃旧值时记录 sequence gap；
- proposal：bounded by count/bytes/expiry，过期直接淘汰并记录；
- allocation plan：ordered/idempotent，不能静默丢失；
- OMS command/event：durable lossless，容量不足关闭 new-risk gate；
- telemetry：bounded lossy + dropped counter；
- cancel/reduce/flatten：独立优先级队列和保留 worker/resource。

## Testing

安全不变量测试禁止依赖不可控 sleep 概率。使用 barrier、latch、virtual clock、fault injector 或可观察状态建立确定性 happens-before。测试必须覆盖 queue full、slow consumer、producer restart、duplicate/out-of-order、epoch change、starvation 和 shutdown drain。
