# Git-discovered component ownership

Status: CURRENT
Implementation: `scripts/check_component_coverage.py`
Tests: `tests/python/test_component_coverage.py`

## Contract and discovery

The verifier reads `git ls-files`, not a caller-supplied source inventory.
All tracked paths are classified as implementation candidates except explicit
support namespaces (`docs/`, `doc/`, `tests/`, `pic/`) and named repository
metadata/license files. A catalog-declared implementation under a support
namespace, such as a runtime JSON policy under `docs/`, remains an owned
production input. A new top-level runtime directory therefore cannot disappear
because its prefix was not previously known.

Each candidate must match an `implementation` path in `docs/module-catalog.json`.
The most specific path owns the file; equally specific different owners fail.
Every module must own a tracked path and have a regular module document.
Repository translation-unit owners in `docs/build-targets.json` must agree;
external SDK units remain explicitly ownerless. Fresh CMake model comparison
is separately owned by `verify_build_ownership.py`.

## Maintenance and limitations

Use the narrowest durable implementation boundary. Directory ownership explains
where a component belongs; it does not prove its API, state or tests are fully
documented. Add or update the human developer contract when behavior changes,
not merely a metadata row. One catalog generates module navigation in
`docs/index.md`; the historical development index is a redirect, not a duplicate
list that requires separate maintenance.

The legacy CSV reporter is retired; the maintained recovery coordinator is
under `HeptaTrade/execution/`. Historical directory names are not evidence of
current runtime ownership.

Fixture tests introduce an unowned runtime path, an unowned script and an
unknown top-level runtime directory, and exercise conflicting/mismatched owners.
Support exclusions are intentional and should remain narrow. Stage new files
before invoking the Git-index check; untracked edits are not repository content.

The lightweight historical `OmsRecover` projection is compiled from
`tests/compat/` by `hepta_execution_coordinator_tests`. It is a test translation
unit owned by that target in both inventories, not a shipped implementation.
The module catalog still lists its behavioral fixtures under OMS tests; the
production journal parser and supported historical schemas are unchanged.

## Independent build-profile refresh

`python3 scripts/verify_build_ownership.py --generate --profile core` regenerates
only core without an IB SDK. Unselected profile snapshots are preserved, not
certified. `--profile ib` requires the real SDK/BID archive; `--profile all`
explicitly observes both. A failed selected profile leaves the previous file
unchanged. Full generated graphs are kept one target per line for focused diffs.

The companion `check_component_coverage.py` selects the same profile (`core` by
default, or explicit `ib`/`all`). An unselected stale graph neither blocks that
profile nor supplies reachability evidence for its missing source. Ownership
is still checked for every tracked production path. Validate IB/all alongside
the actual corresponding CMake profile, never infer IB qualification from core.
