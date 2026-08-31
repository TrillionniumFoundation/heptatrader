# 并发、分片与背压

Status: current target contract
Applies to: hot path, control plane, queues, state writers and scaling
Verification: deterministic scheduler fixtures and performance budgets
Authority: concurrency authority

## 原则

- 逻辑集中、物理分片；
- single-writer authoritative state；
- immutable snapshot/RCU/double buffer 读路径；
- 无跨模块共享可变对象；
- 无跨模块锁；
- 持锁时禁止 Broker、filesystem 和 network I/O；
- correctness 测试禁止依赖 `sleep` 或 OS 调度概率。

```text
Global Capital Allocator
  -> capital pool / risk book
    -> account execution domain
      -> instrument/order actor
```

| 流量 | 顺序/持久性 | 背压 |
|---|---|---|
| market/feature update | per-shard monotonic；可重建 | latest-value/coalescing；暴露 sequence gap |
| StrategyProposal | identity-bound；有 expiry | bounded queue；过期 typed reject |
| AllocationPlan | epoch 内有序、幂等 | single-writer lossless；不得静默丢失 |
| OMS command/event | durable ordered | journal-backed；不得丢失 |
| telemetry | eventual bounded | per-thread/per-shard aggregate |
| cancel/reduce/flatten | 高优先级 | 独立 emergency lane；禁止饥饿 |

Tool Gateway 的 Unix IPC 适合控制调用；行情、feature 和 proposal 高频流量使用独立 shard-aware data plane。
