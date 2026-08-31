# 权威对账

Status: current normative
Applies to: startup, runtime and uncertain recovery
Verification: reconcile fixtures and IB PAPER scenarios
Authority: reconciliation authority

对账比较 Broker open orders/executions、Broker positions/cash/account、OMS journal/replay、local authoritative projections 和 command uncertain set。

```text
block > terminal-latch/manual > warn > converged
```

open-order 或 position mismatch 阻断新风险；现金、FX 或 PnL 缺失按启用规则 fail closed。系统不得仅因本地缓存“看起来合理”而覆盖 Broker-observed truth。

所有 reconcile run 输出 stable reason code、input generations、diff、action 和 completion watermark。
