# Git-discovered component ownership

Status: CURRENT
Implementation: `scripts/check_component_coverage.py`, `scripts/verify_build_ownership.py`
Tests: `tests/python/test_component_coverage.py`, `tests/python/test_build_ownership.py`

## Contract and discovery

`check_component_coverage.py` reads `git ls-files`, not a caller-supplied source
inventory. All tracked paths are classified as implementation candidates except
explicit support namespaces (`docs/`, `doc/`, `tests/`, `pic/`) and named
repository metadata/license files. A catalog-declared implementation under a
support namespace remains an owned production input, so a new top-level runtime
directory cannot disappear merely because its prefix was not previously known.

Each candidate must match an `implementation` path in
`docs/module-catalog.json`. The most-specific path owns the file; equally
specific different owners fail. Every module must own a tracked path and have a
regular module document. Source ownership alone does not claim that C/C++ is
compiled.

## Live build reachability

`verify_build_ownership.py` configures the selected Linux profile and reads the
fresh CMake File API codemodel. Every repository implementation translation unit
must map back to exactly one canonical module. Tests and generated CMake PCH
units retain target ownership; external IB SDK translation units are explicitly
ownerless and accepted only in the IB profile.

The verifier also enumerates tracked C/C++ sources inside module implementation
boundaries. Each must appear in that selected live CMake graph or be explicitly
listed in its owning module's `unbuilt` field. A stale `unbuilt` declaration that
is now compiled is rejected. This keeps the closed-world reachability property
without committing a second 90-KB expansion of every target, dependency and
translation unit.

There is deliberately no checked-in `build-targets.json`. Adding an ordinary
CMake target around already-owned sources therefore needs no generated-graph
update. The actual graph is still validated on every source CI run. When a
diagnostic snapshot is useful, `verify_build_ownership.py --report <path>`
exports the exact observed model; the report is evidence for that run, not an
admission input.

## Profiles

`python3 scripts/verify_build_ownership.py --profile core` requires no IB SDK.
`--profile ib --ib-sdk ... --ib-decimal-library ...` observes the real SDK-enabled
profile and executes its existing configure-time Decimal ABI probe. `--profile
all` explicitly observes both. Core evidence cannot certify IB and an absent SDK
is never converted into a successful skip.

## Maintenance and limitations

Use the narrowest durable implementation boundary. Directory ownership explains
where a component belongs; it does not prove its API, state or tests are fully
documented. Add or update the human developer contract when behavior changes,
not merely a metadata row. One catalog generates module navigation in
`docs/index.md`; the historical development index remains a redirect.

The lightweight historical `OmsRecover` projection under `tests/compat/` is a
test translation unit, not shipped implementation. Build reachability is a
source/build property only; it does not prove runtime readiness, Broker
qualification, target-host behavior or authorization.
