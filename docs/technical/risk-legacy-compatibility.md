# Risk context migration

Status: CURRENT
Applies to: canonical C++11 runtime

The zero-state `PreTradeRiskLegacyWriteOnly<T>` compatibility sinks and the twelve
unbound scalar members have been removed. They no longer silently accept writes.
Retained source writers and tests were migrated; old downstream callers receive a
compiler error instead of a successful assignment that has no effect.

Use `orderNotionalEvidence` for contract/quote/FX-bound converted amounts and
`authoritativeSnapshot` for subject/epoch/generation-bound exposure, PnL and equity.
Set `evaluatedAtMs` only from the Execution-owned decision clock. Missing evidence
still rejects risk increase. Do not recreate scalar defaults or guessed zeroes.

The compiler-backed boundary test rejects every retired member (writes, alias,
pointer, template and pointer-to-member reads) and compiles current evidence reads.
The native risk suite retains actual notional, mixed-subject, freshness, missing
section, hostile numeric and flatten-only behavior tests.
