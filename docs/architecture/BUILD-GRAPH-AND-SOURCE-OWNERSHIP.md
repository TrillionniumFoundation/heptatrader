# Build Graph and Physical Source Ownership

Status: current normative
Applies to: active C/C++ sources, CMake targets, module manifests and tests
Verification: `python3 scripts/check_module_discipline.py` and `python3 scripts/check_cmake_module_graph.py --check --build-dir <configured-build>`
Authority: physical source and configured build-graph governance

## 1. Two ownership layers

Hepta distinguishes **physical source ownership** from **logical contract participation**.

- Every active source file has exactly one physical owner recorded in `docs/modules/source-ownership-registry-v1.json`.
- A ModuleManifest may temporarily claim the same file during an extraction only through an explicit overlap exception.
- An overlap is valid only when every participant uses `shared-migration`, every participant names the same open gap, one physical owner is declared, new participants are forbidden, and an exit milestone is present.
- Directory, exact-file and filename-prefix selectors are different types. Prefix selectors match only within one exact parent directory; `foo` cannot silently claim `foobar/child.cpp`.

Physical ownership decides the DRI for edits, review and source movement. It does not grant runtime authority: the Execution Authority, credential and venue-mutation rules remain unchanged.

## 2. Configured CMake graph

The checked-in manifest is not accepted as proof of the build graph. After CMake configure, `check_cmake_module_graph.py` reads the CMake File API codemodel and validates the actual graph:

```text
configured target
  -> declared target owner
  -> compiled repository source
  -> physical source owner
  -> exact migration exception, when owners differ
  -> declared inter-module dependency
```

A production target that compiles an unowned source, a source from another module without an exact exception, or an undeclared module dependency fails the build. Optional IB targets are validated in the IB PAPER profile and external qualification; their names must still exist in the checked-in CMake source.

## 3. Single-compilation and test-linking rule

Every active production source is compiled by exactly one module-owned production target. Runtime executables and tests consume that implementation only by linking the owning target; direct test compilation of a production `.cpp` is forbidden. OMS, state, intent, typed protocol, session control, event relay, simulator and venue adapter code therefore have explicit module targets rather than source-list duplication.

`source_overlap_exceptions` and `compilation_exceptions` are currently empty. Any future extraction debt must first introduce an open gap, an exact bounded scope, a declared physical owner and an exit milestone; the configured graph and static discipline checks reject an unregistered exception or a stale exception that is no longer observed.

## 4. Path safety

All governed paths are repository-relative POSIX paths. Absolute paths, backslashes, `.`/`..` aliases, symlink escapes, ambiguous equal-priority rules and stale selectors are rejected. Historical `legacy/` sources are outside the active graph and may never satisfy a current source or target declaration.
