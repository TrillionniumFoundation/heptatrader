# 统一数值策略

Status: current target contract
Applies to: portfolio, risk, state, intent, execution and research outputs
Verification: numeric property tests and canonical serialization checks
Authority: numeric semantics authority

- quantity：signed fixed-point microunits；
- money：币种最小单位或显式 fixed decimal scale；
- price：venue tick 或显式 fixed decimal scale；
- rate/FX：值 + observed timestamp + source generation；
- time：UTC epoch milliseconds，duration 使用 monotonic clock；
- percentage/bps：整数或固定 scale；
- overflow：checked arithmetic，fail closed。

优化器和研究内部可以使用浮点，但输出进入可信 runtime 前必须检查 finite、按版本化 rounding policy 量化、canonical sort/serialize、重新计算约束，并输出 numeric-policy version 和 digest。

禁止将 NaN、Inf、隐式二进制浮点容差或平台相关 rounding 带入 permit、command fingerprint 或 journal。
