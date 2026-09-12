# Retired legacy risk inputs

Status: CURRENT  
Applies to: `PreTradeRiskContext` source API

## Removal and migration

The former `PreTradeRiskLegacyWriteOnly<T>` sink and its twelve direct context
members have been removed. A complete repository scan found no production
writers; the remaining writes were regression fixtures and have been migrated.
The public context no longer silently accepts assignments that do nothing.

The removed names are `baseCurrencyOrderNotionalPresent`,
`baseCurrencyOrderNotional`, `snapshotComplete`, `snapshotObservedAtMs`, `nowMs`,
`currentGrossNotional`, `pendingBuyNotional`, `pendingSellNotional`,
`realizedPnl`, `unrealizedPnl`, `peakEquity`, and `currentEquity`.
Names used inside the authoritative section types are not removed.

Use `orderNotionalEvidence` and `authoritativeSnapshot`, binding the configured
subject, connection epoch, generation, freshness, quantity/price units and
quote/FX identity. Do not mechanically copy old scalar assignments into these
objects: the execution authority must establish their provenance first.
All components must be rebuilt together; old binary layout and legacy source
assignment compatibility are intentionally unsupported.

## Failure and regression contract

`tests/python/test_risk_legacy_compatibility_boundary.py` compiles C++11 probes
that reject every removed member, reject each old assignment and its alias,
pointer, template, pointer-to-member and boolean consumption forms, and accept
authoritative reads. Member detection rejects reintroduction under *any* type,
not merely a particular wrapper spelling.

`tests/pre_trade_risk_engine_tests.cpp` retains missing-evidence rejection and
now also tests a supplied converted scalar with an unbound account. Existing
subject, generation, freshness, unit and economic-limit regressions remain.
Removal does not relax any policy or grant PAPER/LIVE authorization.
