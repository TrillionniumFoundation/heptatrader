# Retired runtime compatibility entry

Status: LEGACY
Applies to: explicit rejection of the old CMake include; no buildable runtime
Implementation: `cmake/HeptaLegacy.cmake`
Tests: `tests/python/test_legacy_runtime_boundary.py`, `tests/venue_capability_tests.cpp`, `tests/python/test_legacy_retirement.py`

## Retired execution paths and assets

The old monolith, HeptaStrategy composition, Pegasus simulator, JSONL 0DTE
bridge, watchdog and CSV reconciliation reporter have been removed with their
exclusive CMake graph. The remaining root `Interface/` headers, oneTBB copy,
CTP placeholders and root `Tools/` XML/configuration/market samples are also
retired. No replacement archive, empty compatibility directory or alternative
order transport is installed. Historical source and embedded notices remain
in Git; the final pre-asset-removal source is
`6cdae64e04a92d234852aa14670a54538e9e5f9c`.

The maintained `HeptaTrade/tools/` directory is unrelated and remains intact.
Current CTP/XT capability stubs and `third_party/ctp` are not promoted to real
transports by removing old placeholders.

## The remaining compatibility behavior

Root and standalone HeptaTrade configuration reject enabled historical flags
with `HEPTA_LEGACY_RUNTIME_RETIRED`. Existing explicit OFF invocations still
configure canonical targets. The small `cmake/HeptaLegacy.cmake` entry provides
the same explicit failure to an old include caller. It is tested behavior,
not an active second build graph or an empty module completion marker.

## Maintained recovery is not Legacy

OMS historical readers, encrypted HSL migrations, stable command identities,
terminal witnesses and authoritative reconciliation are unchanged. No old
persistent format is retired by this source cleanup. Users of old applications
must retain their exact artifact/state and arrange a separate migration.

## Consumer and acceptance boundary

See [retirement scope and evidence](../technical/legacy-retirement.md) for the
consumer map. The retired application and watchdog were the known users of
`hepta*.h` and TinyXML. Canonical targets declare their own includes and use an
external pinned IB SDK, not these root overlays. Removal is tested by the real
core build, both sanitizer lanes, fresh install/process acceptance and the
existing CMake entry-point tests. Those tests also inspect generated include
paths to prevent the retired directories from being an implicit dependency.

This supports the maintained source profiles, not every external fork, old
binary or independently licensed dataset. Historical contents are not copied
into new release or evidence archives. Target-host rollback and separately
supplied SDK-linked IB qualification remain independent acceptance scopes.
