# Deterministic Risk Policy V2

Status: current core contract; target portfolio expansion
Applies to: portfolio, intent, Execution and venue-specific stricter rules
Verification: risk property, boundary, snapshot and parity tests
Authority: risk policy authority

每个启用规则必须具备：

```text
field -> authoritative owner -> generation/freshness -> missing behavior
      -> stable reason code -> tests
```

必需维度包括 quantity/order shape、reference price、rate/active orders、gross/net position、strategy/portfolio budget、margin/FX、daily PnL/drawdown、liquidity、snapshot completeness、kill/submission/flatten mode。

组合级风险在 Global Decision Plane 约束 AllocationPlan；最终 deterministic pre-trade risk 在 Execution Authority 重新验证。上层 allow 不强迫 Execution allow。

strict reduction 必须证明 projected gross 减少且不跨零建立反向敞口。未知数据不视为零；非 finite、overflow、stale generation 和未接线规则一律拒绝风险增加。
