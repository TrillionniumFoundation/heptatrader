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

## Implemented boundary

`hepta.numeric.fixed-v1` 使用有符号 64 位 microunits（scale `1,000,000`），并把 raw 范围限制在 `±9,000,000,000,000,000`，使兼容 double 在规范化后仍保留单 microunit 整数身份。typed C++ wire 与 Python/MCP bridge 均从`schemas/tool-catalog-v1.json` 取得同一 numeric policy；编码输出 canonical decimal，解码拒绝 NaN、Inf、负零、范围溢出和超过六位小数的 scale mismatch。旧执行结构中的 double 仅是固定点验证后的兼容投影，不再是跨信任边界的权威表示。

## Checked raw construction and binary64 projection

Fixed microunits are authoritative. Runtime code constructs a fixed value only through canonical decimal parsing or a checked raw factory. An unchecked public raw constructor is forbidden, and every raw value is range-checked before entering a trusted boundary.

Binary64 is compatibility output only. Projection succeeds only when canonical conversion back to fixed produces the identical raw microunuit. Collapse, scale mismatch, non-finite values, signed-zero ambiguity and range loss return typed failure. Allocation, risk, accounting, snapshot identity and digest logic never use binary64 as authority.
