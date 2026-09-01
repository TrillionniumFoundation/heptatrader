# Numeric Policy

Status: current target contract
Applies to: market data, proposals, allocation, portfolio, risk, intent, OMS and venue conversion
Verification: boundary, exact-projection, overflow, replay and cross-language parity tests
Authority: trusted numeric representation

## Boundary types

- quantity/position：signed fixed-point units，scale 由 instrument contract 版本化；
- price：integer ticks 或 decimal fixed-point，绑定 tick size；
- money：currency minor/fixed units，禁止无币种数值；
- rate/bps：整数 fixed scale；
- time：UTC epoch milliseconds + monotonic duration；
- FX：rate、base/quote currency、observed time、generation 和 source digest。

优化器内部可以使用 IEEE floating point，但输入需规范化，输出必须通过 finite/range 检查、canonical quantization、independent constraint validator 和 deterministic risk。NaN、Inf、overflow、scale mismatch、未知 rounding 或 stale FX 对风险增加 fail closed。

## Canonical operations

加总顺序、排序、round-half policy、tick rounding、zero tolerance、comparison tolerance 和 hash serialization 全部版本化。跨语言 binding 必须对 golden vectors 逐字节一致。不得把“接近零”用于绕过 zero-crossing reduction 证明。

## Implemented fixed boundary

`hepta.numeric.fixed-v1` 使用有符号 64 位 microunits（scale `1,000,000`），并把 authoritative raw 范围限制在 `±9,000,000,000,000,000`。这个范围是固定点运算和序列化边界，**不表示整个范围可以按单 microunit 间隔无损映射到 IEEE binary64**。高数量级处的 binary64 spacing 可大于一个 microunit，因此两个不同 raw value 可能在普通除法投影后碰撞。

为消除该隐式窄化：

- unchecked public raw constructor 不存在；外部 raw 输入必须经 `FromRawExact` 做范围检查；
- decimal 输入经 canonical grammar 和 exact scale 解析；
- market/event 等公开 aggregate 在接受边界重新检查每个 fixed value 的 invariant；
- authoritative hash、比较、约束与持久化始终使用 raw fixed value；
- legacy double 兼容面只能调用 `ToDoubleExact`。

`ToDoubleExact` 先生成候选 binary64，再通过 `FromDoubleExact` 回转，并要求恢复出的 raw 与原值逐位相同。失败返回 `NUMERIC_DOUBLE_PROJECTION_LOSS`，不得截断、近似或继续风险增加。由于任何成功投影都必须回到唯一相同 raw，成功接受的兼容子集是 injective；无法证明这一点的值保持固定点表示或 fail closed。typed C++ wire 与 Python/MCP bridge 从 `schemas/tool-catalog-v1.json` 取得同一 numeric policy，编码使用 canonical decimal，解码拒绝 NaN、Inf、负零、范围溢出和超过六位小数的 scale mismatch。
