# TargetPositionIntent V1

Status: current core contract
Applies to: ordinary Agent compatibility path and final Execution intake
Verification: snapshot, permit, idempotency and negative-path tests
Authority: ordinary Agent mutation contract

普通 Agent 可表达 instrument、target_position、max_slippage_bps、expires_at_ms 和可选 urgency bounds。Agent 不可提供 authoritative current position、account value、quote、risk usage、final quantity、venue route 或 Broker order ID。

```text
get authoritative snapshot
  -> normalize target
  -> derive delta
  -> portfolio/risk evaluation
  -> issue opaque preview permit and mutation_command_id
  -> apply with exact same normalized intent
  -> revalidate generation/fence/policy
  -> persist permit consumption and command
  -> venue send
```

Permit 单次跨 command ID 使用；只有已接受命令的相同重试可以 replay。过期、缺失、已消费、跨命令、输入变化或 generation 变化都返回稳定 rejection。no-op 不发送 venue command。
