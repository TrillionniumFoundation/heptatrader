# AuthoritativeSnapshot V2

Status: current core/target contract
Applies to: Execution state authority, risk, intent, portfolio and Global Decision
Verification: schema, generation, watermark, freshness, concurrent-update and replay tests
Authority: authoritative decision-state contract

Snapshot 只能由 Execution-owned state authority 组装。Gateway、Agent、策略和 allocator 可以请求或引用，不能供应、覆盖或扩展权威字段。

## Envelope

包含 execution epoch、fencing generation、state generation、collection/event watermark、collection window、captured/fresh-until time、account/execution domain、component digests 和 aggregate payload digest。

## Payload

至少包含 normalized quote/liquidity、account/cash/PnL/margin、positions、active/recent orders、risk limit/usage、venue/connection/reconcile/kill state。内部组件是 typed values；JSON 仅是验证后的边界序列化。

## Atomicity

fill、cancel、correction、reconnect 或 restart 在 capture 期间发生时，要么完整进入同一 generation，要么 capture 失败。调用者不得混合多个 snapshot。跨 shard 的 Global Decision 使用明确 SnapshotVector，并验证每个 component 的 temporal compatibility。

## Admission

quote positive/ordered/instrument-bound/fresh；required account/order/position/risk complete；时间和 watermark 单调；size bounded；digest match。任一失败返回 `DECISION_SNAPSHOT_*` 且 `authoritative` 不得为 true。

机器 schema 为 `schemas/authoritative-snapshot-v2.json`。
