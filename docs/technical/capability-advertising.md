# Capability and venue-advertising contract

Status: CURRENT
Applies to: `docs/capabilities.json` schema v2
Implementation: `docs/capabilities.json`, `scripts/check_documentation_core.py`
Tests: `tests/python/test_documentation_control_plane.py`, `tests/python/test_legacy_runtime_boundary.py`

## Separate decisions

The capability matrix keeps three independent booleans so consumers cannot
turn an implemented transport into an authorization claim. `transport_implemented`
means source code can speak the named transport. `advertisable` means the
capability may be presented to an operator as an available venue. `authorized`
means the source and host controls permit mutation. A capability that requires
external qualification is never source-authorized.

`QUALIFICATION_REQUIRED` IB PAPER therefore has an implemented C++ transport,
but remains non-advertisable and unauthorized until a separately reviewed,
Broker-observed campaign produces an external receipt. Experimental CTP and
XT/QMT entries have no transport, are non-advertisable, and are unauthorized.
The deterministic simulator is local-only: its transport is implemented for
tests but it is not a real venue and cannot authorize Broker mutation. LIVE is
explicitly unavailable in every field and at the matrix root.

## Validation and compatibility

The documentation control-plane validator enforces that advertising requires
both transport and authorization, external qualification cannot coexist with
source authorization, and non-current statuses remain fail-closed. It accepts
the historical v1 shape only for migration fixtures; checked-in capability
data must use schema v2. Callers should read the three v2 fields and ignore the
removed `advertise_as_real_venue` shortcut.
