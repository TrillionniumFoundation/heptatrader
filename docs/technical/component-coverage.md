# Git-discovered component coverage

Status: CURRENT  
Implementation: `scripts/check_component_coverage.py`  
Tests: `tests/python/test_component_coverage.py`

## Contract

The verifier obtains the exact tracked file set from `git ls-files`; it does not accept a caller-authored list of supposedly complete components. Production paths are selected from maintained runtime, adapter, build, deployment, plugin, script, workflow, vendor, and legacy roots.

Each production path must match at least one `implementation` path in `docs/module-catalog.json`. The most specific matching path owns the file. Equal-specificity ownership by different modules is rejected. Every module must own at least one tracked production path and have a regular module document.

For every repository implementation translation unit in `docs/build-targets.json`, the inferred Git/module owner must equal the CMake inventory owner. External SDK translation units remain explicitly ownerless.

## Change procedure

A change that creates a new production root or component must update the module catalog and its technical documentation in the same commit. Expanding a broad catch-all solely to silence the checker is not acceptable; use the narrowest durable component boundary. The development documentation index must name every registered module document.

## Hostile coverage

Fixture tests add an unowned `HeptaTrade/new_component` path, an unowned runtime script, alter a CMake owner, and remove a module from the development index. Each case must fail with a path-specific reason.
