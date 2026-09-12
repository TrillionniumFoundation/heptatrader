# Legacy runtime boundary

Status: LEGACY  
Applies to: repository HEAD  
Implementation: `HeptaStrategy/`, `HeptaSimulator/`, `Interface/`, `Tools/`, `HeptaTrader.sln`, `HeptaTrader_Linux.sln`, selected top-level legacy files under `HeptaTrade/`  
Tests: `tests/python/test_legacy_runtime_boundary.py`, `tests/venue_capability_tests.cpp`

## Responsibilities

This module records the historical monolith, strategy platform, Pegasus simulator, Visual Studio assets, vendored compatibility headers, large static instrument data, and deprecated bridges that remain in the repository. Its purpose is compatibility and migration analysis, not canonical runtime delivery.

## Public interfaces

Legacy build profiles are exposed only through explicit CMake options. They are off by default. No legacy project file, configuration example, adapter scaffold, or historical binary is an authority-bearing installation interface.

## State and persistence

Legacy XML, solution files, market-data examples, and compatibility headers are repository assets. They are not read automatically by the maintained Agent OS runtime unless a separately documented legacy profile is enabled.

## Failure semantics

A legacy component that is absent, incompatible, or disabled must not cause the canonical core build to fall back to a historical order path. CTP and XT/QMT scaffolds return explicit no-transport failures rather than manufacturing connection, query, or order success.

## Security boundaries

The maintained Execution Service remains the sole order authority. Legacy monoliths, strategies, bridges, and project files may not bypass the Tool Gateway, journal-before-send, risk, kill switch, or reconciliation boundaries. `production_authorized=false` is permanent for this module.

## Observability

Capability output distinguishes CURRENT, QUALIFICATION_REQUIRED, EXPERIMENTAL, LEGACY, and UNAVAILABLE. Operators should treat a legacy build flag, imported project, or example configuration as a compatibility signal rather than a deployment-ready status.

## Test expectations

Tests lock the default-off CMake options, experimental adapter no-transport behavior, PAPER qualification requirement, and LIVE unavailability. Component coverage also requires every retained legacy production path to have an explicit owner instead of being silently omitted.

## Known limitations

The legacy source is not comprehensively modernized, benchmarked, or supported across current compilers. Large data assets and old IDE projects should eventually be archived or moved to versioned external artifacts after reproducibility and licensing are documented.

## Isolated build ownership and retirement

Legacy-only translation-unit and proprietary-overlay declarations live in
`cmake/HeptaLegacy.cmake`, included only when the monolith is explicitly enabled.
The canonical profile does not evaluate that target declaration. Existing
historical source and provenance remain available; no proprietary SDK or old
binary is shipped or recreated by this cleanup.

Physical source archival requires an owner-confirmed consumer inventory and a
last-supported revision. No such consumer sign-off is implied by absence from
the default build. CTP/XT refusal paths and the default-off flags remain tested.
