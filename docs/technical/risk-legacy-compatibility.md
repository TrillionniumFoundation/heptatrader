# Retired risk scalar API

Status: CURRENT
Applies to: source migration to the authoritative risk context

## Removal

The write-only `PreTradeRiskLegacyWriteOnly<T>` sink and its twelve context
members have been removed. The audited tree had no production assignment
writers; remaining assignments were regression fixtures. They no longer compile,
rather than appearing to accept risk configuration while silently discarding it.
This is a source-breaking cleanup, not a relaxation of risk evidence.

The removed names are `baseCurrencyOrderNotionalPresent`,
`baseCurrencyOrderNotional`, `snapshotComplete`, `snapshotObservedAtMs`, `nowMs`,
`currentGrossNotional`, `pendingBuyNotional`, `pendingSellNotional`, `realizedPnl`,
`unrealizedPnl`, `peakEquity` and `currentEquity` on **PreTradeRiskContext**.
Corresponding bound values inside authoritative section types remain supported.

## Migration

Use `PreTradeRiskAuthoritativeSnapshot` and `PreTradeRiskOrderNotionalEvidence`.
The execution authority must assemble subject/account/venue/currency/instrument
identity, connection epoch, snapshot generation, source timestamps and unit,
quote and FX provenance. Copying an old unbound scalar into a new field does not
make its evidence authoritative. The execution-owned evaluation clock is
`PreTradeRiskContext::evaluatedAtMs`, not a timestamp supplied by the evidence.

Every canonical component must be rebuilt together; mixed old/new C++ object
layouts are unsupported. No on-disk OMS or session format changes in this API
retirement, and PAPER/LIVE authorization is unchanged.

## Behavior and verification

`tests/python/test_risk_legacy_compatibility_boundary.py` compiles a positive
probe for the authoritative API and requires each retired assignment to fail
C++11 compilation. No aliases, getters or compatibility storage are retained.
`tests/pre_trade_risk_engine_tests.cpp` still executes the missing identity,
notional, freshness, subject, unit, pending exposure and boundary cases. Those
behavior tests, not source spelling, protect the risk model.
