# OMS Journal 与命令生命周期 V3

Status: current core contract
Applies to: OMS, Execution, state projection and replay
Verification: journal durability, crash/replay, idempotency and migration tests
Authority: OMS durability authority

Durable command 至少绑定 command ID、normalized payload digest、owner/domain、epoch/fence、snapshot/permit reference、accepted timestamp、operation、status、reason、venue correlation 和 schema version。

生命周期：

```text
received -> rejected
received -> durable-accepted -> send-attempted
         -> acknowledged / partially-filled / filled / cancelled / rejected
         -> uncertain -> reconciled-terminal or terminal-latched
```

journal append 成功必须早于首次 venue send。相同 command ID + 相同 payload 返回 durable replay；相同 ID + 不同 payload 冲突。event replay 使用 canonical ordering 和 stable event identity。schema major 变化必须提供 migration/replay fixture。
