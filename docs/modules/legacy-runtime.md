# Legacy runtime boundary

Status: LEGACY
Applies to: repository HEAD
Implementation: `HeptaStrategy/`, `HeptaSimulator/`, `Interface/`, `Tools/`, `HeptaTrade/reconcile/`, selected top-level legacy files under `HeptaTrade/`
Tests: `tests/python/test_legacy_runtime_boundary.py`, `tests/venue_capability_tests.cpp`

## Responsibilities

This module records the historical monolith, strategy platform, Pegasus simulator, vendored compatibility headers, large static instrument data, and deprecated bridges that remain in the repository. Its purpose is compatibility and migration analysis, not canonical runtime delivery.

## Public interfaces

Legacy build profiles are exposed only through explicit CMake options. They are off by default. No legacy project file, configuration example, adapter scaffold, or historical binary is an authority-bearing installation interface.

## State and persistence

Legacy XML, market-data examples, and compatibility headers are repository assets. They are not read automatically by the maintained Agent OS runtime unless a separately documented legacy profile is enabled.

## Failure semantics

A legacy component that is absent, incompatible, or disabled must not cause the canonical core build to fall back to a historical order path. CTP and XT/QMT scaffolds return explicit no-transport failures rather than manufacturing connection, query, or order success.

## Security boundaries

The maintained Execution Service remains the sole order authority. Legacy monoliths, strategies, bridges, and project files may not bypass the Tool Gateway, journal-before-send, risk, kill switch, or reconciliation boundaries. `production_authorized=false` is permanent for this module.

## Observability

Capability output distinguishes CURRENT, QUALIFICATION_REQUIRED, EXPERIMENTAL, LEGACY, and UNAVAILABLE. Operators should treat a legacy build flag, imported project, or example configuration as a compatibility signal rather than a deployment-ready status.

## Test expectations

Tests lock the default-off CMake options, experimental adapter no-transport behavior, PAPER qualification requirement, and LIVE unavailability. Component coverage also requires every retained legacy production path to have an explicit owner instead of being silently omitted.

## Known limitations

The legacy source is not comprehensively modernized, benchmarked, or supported across current compilers. Eight unused Visual Studio solution/project assets have been removed; their exact provenance and the shared sources deliberately retained are documented in [`../technical/legacy-retirement.md`](../technical/legacy-retirement.md). Large data assets remain pending consumer and licensing analysis rather than being blindly deleted.
