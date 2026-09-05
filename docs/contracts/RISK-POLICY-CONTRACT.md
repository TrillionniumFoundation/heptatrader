# Deterministic Risk Policy V2

Status: current core contract; target portfolio expansion
Applies to: portfolio, intent, Execution and venue-specific stricter rules
Verification: risk property, numeric-boundary, snapshot and parity tests
Authority: risk policy authority

每个启用规则必须具备：

```text
field -> authoritative owner -> generation/freshness -> missing behavior
      -> stable reason code -> tests
```

必需维度包括 quantity/order shape、reference price、rate/active orders、gross/net position、strategy/portfolio budget、margin/FX、daily PnL/drawdown、liquidity、snapshot completeness、kill/submission/flatten mode。

组合级风险在 Global Decision Plane 约束 AllocationPlan；最终 deterministic pre-trade risk 在 Execution Authority 重新验证。上层 allow 不强迫 Execution allow。

## Canonical numeric boundary

风险业务规则只在 `hepta.numeric.fixed-v1` microunits 上执行。现有 venue/legacy 调用面的 `double` 字段只是兼容 ingress：进入任何 limit、notional、position、PnL、drawdown、reduction 或 price-deviation 判断之前，必须精确归一化为一个合法 raw microunit。NaN、Inf、负零、超出固定点范围、超过六位尺度、不能从 canonical fixed 值逐位回投为同一 binary64 的输入一律 fail closed。

notional 使用有符号 128 位中间整数比较，price-deviation 使用整数交叉乘法。业务判断禁止 epsilon、相对误差或 portfolio-scale tolerance。兼容 ingress 不获得数值权威，digest、限额比较和 reduction 证明均以固定点 raw 值为准。

`deterministic-risk-v3` 是本契约下的兼容性加固实现：保留既有 reason-code 优先级和外部调用形状，同时移除浮点业务运算。未来移除 binary64 ingress 时需要新的受治理接口版本。

## Strict reduction

strict reduction 必须同时证明：

1. signed instrument position 等于 current position 加上该 order 的 exact signed quantity；
2. projected position 不跨零建立反向敞口；
3. projected gross 严格下降；
4. `projected_gross + exact_quantity == current_gross` 逐 microunit 成立。

未知数据不视为零；numeric conversion、overflow、stale generation、incomplete snapshot 和未接线规则一律拒绝风险增加。Kill switch、submission-disabled、rate/active-order exhaustion 只能为已证明的 strict reduction 保留安全退出通道，不能降低其他校验。
