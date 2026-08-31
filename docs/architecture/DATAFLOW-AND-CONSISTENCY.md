# 数据流与一致性

Status: current normative
Applies to: market data, authoritative state, proposals, plans and execution events
Verification: snapshot, replay, event-ordering and reconciliation tests
Authority: consistency authority

```text
venue event
  -> adapter normalization
  -> authoritative state writer
  -> OMS/reconciliation projection
  -> generation-consistent snapshot
  -> strategy proposal / intent evaluation
  -> AllocationPlan
  -> permit + durable command
  -> venue send
```

- 每个 execution domain 有一个 authoritative writer。
- snapshot 包含 epoch、fence、state generation、collection/event watermark 和 freshness。
- capture 期间发生 fill、cancel、correction、reconnect 或 restart 时，要么完整进入同一 generation，要么 capture 被拒绝。
- caller 不得拼接多个 generation。
- proposal 和 plan 必须绑定输入 snapshot/vector digest 与 expiry。
- duplicate/out-of-order venue event 通过 stable event identity、sequence 和 reconcile 收敛。
- uncertain outcome 不触发盲目重发；使用相同 command ID 查询或对账。

数据一致性优先于可用性；风险增加遇到不完整数据时 fail closed。
