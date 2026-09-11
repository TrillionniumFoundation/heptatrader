# Risk legacy compatibility boundary

Status: CURRENT  
Applies to: `PreTradeRiskContext` migration surface only

## Purpose

`PreTradeRiskContext` still exposes a small set of historical member names that predate the authoritative snapshot and converted-notional evidence contracts. Historical callers and hostile regression fixtures may continue to assign those names during migration, but the members are not scalar storage and are not a second risk input model.

The canonical readable risk path is `PreTradeRiskOrderNotionalEvidence` plus `PreTradeRiskAuthoritativeSnapshot`, with subject, connection epoch, generation, freshness, unit, quote and FX identity bound explicitly.

## Compatibility-only fields

The temporary legacy surface consists of:

- `baseCurrencyOrderNotionalPresent` and `baseCurrencyOrderNotional`;
- `snapshotComplete`, `snapshotObservedAtMs` and `nowMs`;
- `currentGrossNotional`, `pendingBuyNotional`, `pendingSellNotional`;
- `realizedPnl`, `unrealizedPnl`, `peakEquity`, `currentEquity`.

Each name is a `PreTradeRiskLegacyWriteOnly<T>`. The wrapper accepts the historical assignment syntax but intentionally stores no value and exposes no primitive conversion, getter, arithmetic, comparison or boolean authority. No scalar value from this surface can satisfy an enabled order-notional, gross-notional, daily-loss or drawdown policy.

## Compile-time authority boundary

The compatibility restriction lives in the C++ type system rather than in a variable-name scanner. A canonical consumer cannot recover a primitive value through a renamed reference, direct pointer, parenthesized dereference, helper/template with inferred type, pointer-to-member indirection, or boolean control flow. Those operations are C++ type errors regardless of the spelling of the `PreTradeRiskContext` variable.

`tests/python/test_risk_legacy_compatibility_boundary.py` invokes the configured compiler with the same C++11 language level as the canonical runtime. Positive probes prove assignment-only historical callers still compile, authoritative snapshot reads remain valid, and the wrapper is an empty type with no conversion to `bool`, `double` or `std::int64_t`. Negative probes require alias, pointer/dereference, helper/template, pointer-to-member and boolean consumption attempts to fail compilation.

The full canonical build independently proves the production translation-unit set builds against this API. The same boundary test requires `PreTradeRiskOrderNotionalEvidence` and `PreTradeRiskAuthoritativeSnapshot` to remain present in the public context and consumed by the risk implementation.

## Removal rule

The compatibility members may be physically removed from `PreTradeRiskContext` when the remaining historical assignment-only writers have been migrated or retired in the same reviewed change. Removal must not be combined with a relaxation of authoritative evidence requirements. Until physical removal, write-only, zero-state compatibility is mandatory.

## Failure semantics

Any canonical source that attempts to consume a compatibility member as a bool, integer or floating-point value fails C++ compilation. Adding an alias, helper, template or member pointer cannot create an exception because the restriction is carried by the member type. A developer must use the authoritative evidence types instead.

## Security and testing

The boundary prevents shortcuts such as caller-supplied `snapshotComplete=true`, unbound notional, PnL or equity values from silently bypassing subject/generation/freshness/unit provenance. Existing hostile C++ tests may assign compatibility members solely to prove those writes have no authority. Product code has no readable value available from them. Compiler-backed hostile probes cover the ordinary C++ indirection forms identified during external review.

## Known limitation

This migration preserves assignment-source compatibility, not primitive read compatibility or old binary layout compatibility. Canonical HeptaTrader components are rebuilt from one reviewed source object; mixing old and new object files is unsupported. Physical member deletion remains a later non-authorizing maintenance task after old writers are gone. `paper_authorized=false` and `live_authorized=false` are unaffected by this source-only boundary.
