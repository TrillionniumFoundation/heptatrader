# EventEnvelope 与顺序契约 V1

Status: current target contract
Applies to: data plane, OMS, state, proposals and lifecycle events
Verification: ordering, duplicate, gap and replay tests
Authority: cross-module event contract

每个跨模块事件至少包含 schema、event_id、producer_module/epoch、shard_key、sequence、authoritative_ts_ms、observed_at_ms、causation/correlation ID、payload digest 和 payload。

- `sequence` 在 producer epoch + shard 内单调；
- 相同 event ID + digest 幂等；相同 ID + 不同 digest 冲突；
- gap 必须显式暴露，不能把缺失事件当成零值；
- out-of-order event 可以缓冲、拒绝或 reconcile，但策略必须在 manifest 中声明；
- OMS/Execution event lossless durable；
- market/feature event 可 coalesce，但保留最新 sequence 与 gap counter；
- replay 使用 canonical order：authoritative timestamp、producer epoch、shard、sequence、event ID。
