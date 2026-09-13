# Repository control and source verification

Status: CURRENT
Applies to: repository HEAD
Implementation: `.github/`, `.agents/`, `scripts/`
Tests: `tests/python/test_component_coverage.py`, `tests/python/test_documentation_control_plane.py`, `tests/python/test_gap_register.py`, `tests/python/test_qualification_trust_boundary.py`

## Responsibilities

Repository control defines the source-side admission contract: exact revision checkout, module and capability truth, build-target ownership, component coverage, gap evidence, release verification, and the optional qualification workflow boundary. It does not create Broker authority.

The component-coverage verifier discovers production paths from the exact Git index and compares them with module implementation ownership. A new tracked production path therefore cannot disappear merely because it was omitted from a hand-maintained catalog.

## Public interfaces

- `scripts/check_component_coverage.py` validates Git-discovered production ownership and CMake translation-unit owner agreement.
- `scripts/check_documentation.py` validates module metadata, capabilities, links and the catalog-generated index; `--write-index` explicitly regenerates navigation. It does not score technical prose depth.
- `scripts/check_gap_register.py` validates an extensible issue inventory and authorization invariants. Open issues are allowed; only an explicit `--release-profile` check rejects its scoped unresolved blockers.
- `scripts/verify_build_ownership.py` compares fresh CMake File API output with `docs/build-targets.json`.
- Maintained workflows execute those controls on pull-request, main, and merge-group subjects as applicable.

## State and persistence

Canonical source truth is stored in versioned JSON and Markdown under `docs/` and `.github/`. CI logs and artifacts are evidence for one exact revision; they are not persistent runtime state and never authorize a Broker session.

## Failure semantics

Missing ownership, an unknown module, ambiguous longest-path ownership, build-inventory owner drift, an omitted module document, invalid JSON, a stale capability claim, or a missing required check fails the source gate. No failure is converted into PAPER or LIVE authorization.

Capability truth uses schema v2's separate `transport_implemented`,
`advertisable`, and `authorized` fields. An implemented transport is evidence
of code only; external qualification and host controls are required before a
venue can be advertised or authorized. The control-plane validator rejects
any matrix that collapses these decisions or marks a non-current capability as
authorized.

## Security boundaries

Repository credentials, review settings, CI status, host credentials, runner custody, and Broker evidence are separate trust domains. Source verification may prove repository properties only. It cannot synthesize independent review, Merge Queue admission, host identity, PAPER account mode, or a broker-observed campaign.

## Observability

Failures identify the exact path, target, module, document, invalid fact or release blocker. Successful output is intentionally small and is bound by the workflow to the exact checked-out SHA.

## Test expectations

Tests create hostile fixture repositories with newly tracked unowned components, mismatched CMake owners, and incomplete documentation indexes. The production repository is also checked directly through Git rather than an exported file list.

## Known limitations

GitHub-side review, Ruleset, Merge Queue, environment, and runner controls remain server-side facts. The source tree can describe and verify their intended interfaces but cannot force a different principal to approve or make an offline runner available.

## CI responsibility and limits

Python ownership is defined once by `scripts/run_python_tests.py`: core,
source, install and isolated process are disjoint and exhaustive partitions.
Core Runtime CI executes core/install/process plus native and real systemd
acceptance; Documentation Control Plane executes source plus structural checks.
Documentation Control Plane also owns the shell-syntax and before/after
exact-index observations; the standalone Qualification Source Audit is retired. GCC and Clang sanitizers remain independent
native executions, not duplicate Python discovery.

The optional PAPER workflow is parsed as YAML by
`scripts/check_qualification_trust_boundary.py` (development dependency:
`python3-yaml`). It checks supported execution phases, exact bindings, protected
runner/environment custody and publication paths. Human step names and harmless
condition ordering are not contracts. It deliberately accepts only a restricted
direct-command shell form, not arbitrary Bash. Subprocess tests execute the
actual workflow command blocks using inert stubs and require nonzero failure to
propagate. This is source-side engineering evidence, not proof of a live Broker
campaign or of every possible GitHub Actions expression.

The former static source-token closure verifier has been removed. Behavioral
correctness belongs to executed tests, not required private function names,
formula spelling or historical test messages. A valid issue inventory may
contain OPEN, ACCEPTED or DEFERRED work. A green structural check does not
constitute universal project completeness or external qualification.
