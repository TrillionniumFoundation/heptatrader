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

The legacy CSV reporter now belongs to `legacy-runtime`; the maintained
recovery coordinator is under `HeptaTrade/execution/`. Do not infer current
behavior from a similarly named directory.

Fixture tests introduce an unowned runtime path, an unowned script and an
unknown top-level runtime directory, and exercise conflicting/mismatched owners.
Support exclusions are intentional and should remain narrow. Stage new files
before invoking the Git-index check; untracked edits are not repository content.
