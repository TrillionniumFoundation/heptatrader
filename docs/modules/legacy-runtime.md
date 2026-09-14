# Retained shared compatibility material

Status: LEGACY
Applies to: retained shared headers/data, not a buildable trading runtime
Implementation: `Interface/`, `Tools/`, `cmake/HeptaLegacy.cmake`
Tests: `tests/python/test_legacy_runtime_boundary.py`, `tests/python/test_legacy_retirement.py`, `tests/venue_capability_tests.cpp`

## Retired execution paths

The old monolith, HeptaStrategy static-library composition, Pegasus simulator,
JSONL 0DTE bridge, legacy strategy orchestration, watchdog and CSV reconciliation
reporter have been removed with their dedicated XML configuration/data. Their
CMake include/link/overlay machinery is removed too, not hidden behind another
success flag. Historical source remains in Git at
`3ada6d3157d603e63a47f660d41bfb3937bbec9c`.

Root and standalone HeptaTrade configuration reject enabled historical flags
with `HEPTA_LEGACY_RUNTIME_RETIRED`. Existing explicit OFF invocations still
configure the canonical runtime. There is no automatic redirection of a legacy
configuration or order request into the maintained execution service.

## Intentionally retained boundary

`Interface/` and `Tools/` remain a separately labelled compatibility/provenance
collection. Their presence does not claim a supported binary, deployment or
redistribution licence. This retirement does not assert that every third-party
header/data consumer or external deployment has been inventoried. A later
removal must check the retained consumers and licensing separately.

The tiny `cmake/HeptaLegacy.cmake` file is an explicit failure diagnostic for
old include callers, not a second build definition. Default builds do not load
it. No CTP SDK overlay, alternate Broker runtime or mutable download replaces
the retired targets.

## Maintained recovery is not Legacy

OMS schema readers, encrypted HSL lease migrations, stable command identity,
terminal witnesses and authoritative reconciliation remain maintained runtime
behavior. They were not removed with the old CSV reporter or monolith. Do not
infer that an old on-disk tag is unused because an old application was retired.
Canonical CTP/XT fail-closed capability stubs and the separate SHADOW research
components are also unchanged.

## Acceptance and migration

See [retirement scope and evidence](../technical/legacy-retirement.md). Tests
execute the real CMake entry points: enabled retired flags fail explicitly,
while supported configuration with OFF flags produces the canonical targets.
The full core build, GCC/Clang sanitizer suites, source ownership inventory,
fresh install and real process acceptance remain the integration checks.
Users of an old binary must retain its exact source/artifact and state and
perform an explicit migration; deleting source is not a state migration.
