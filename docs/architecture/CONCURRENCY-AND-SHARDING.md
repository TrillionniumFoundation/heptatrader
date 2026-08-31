# 并发、分片与 Backpressure

Status: current target contract
Applies to: data plane, strategies, Global Decision, state, OMS, Gateway and Execution
Verification: deterministic scheduler, ordering, overload, fairness and emergency-lane tests
Authority: concurrency semantics

## Ownership model

- execution domain、capital pool、market-data shard 和 module instance 均有唯一 active writer/leader；
- readers 使用 immutable generation snapshot、RCU/double-buffer 或明确 actor message；
- 跨模块共享 mutable pointer、全局 map 和无 owner mutex 禁止；
- module internal lock 不跨 module call、network/Broker/filesystem I/O；
- leader change 增加 epoch/fence，旧 writer 的输出被拒绝。

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
