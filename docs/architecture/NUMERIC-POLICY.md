# Numeric Policy

Status: current target contract
Applies to: market data, proposals, allocation, portfolio, risk, intent, OMS and venue conversion
Verification: boundary, rounding, overflow, replay and cross-language parity tests
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

## Migration

当前 mixed-double 接缝标记为 G-NUM-001。迁移按 boundary type → adapter conversion → portfolio/risk → intent/permit → journal/event 顺序进行，并用 golden replay 证明经济和执行语义差异。
