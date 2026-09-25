# Repository control and source verification

Status: CURRENT
Applies to: repository HEAD
Implementation: `.github`, `.agents`, `scripts/README.md`, `scripts/check_component_coverage.py`, `scripts/check_documentation.py`, `scripts/check_gap_register.py`, `scripts/dev_core.sh`, `scripts/verify_build_ownership.py`, `scripts/verify_exact_git_index.py`, `scripts/run_python_tests.py`, `scripts/render_protocol_reference.py`, `scripts/plan_owner_ruleset.py`, `scripts/source_json.py`, `scripts/ci_change_scope.py`
Tests: `tests/python/test_component_coverage.py`, `tests/python/test_documentation_control_plane.py`, `tests/python/test_gap_register.py`, `tests/python/test_qualification_trust_boundary.py`, `tests/python/test_documentation_structure.py`, `tests/python/test_python_test_partition.py`, `tests/python/test_ib_workflow_interfaces.py`, `tests/python/test_protocol_reference.py`, `tests/python/test_owner_ruleset_plan.py`, `tests/python/test_source_workflow_commands.py`, `tests/python/test_source_json.py`

## Responsibilities

Repository control defines the source-side admission contract: exact revision checkout, module and capability truth, live build ownership, component coverage, gap evidence, release verification, and the optional qualification workflow boundary. It does not create Broker authority.

The component-coverage verifier discovers production paths from the exact Git index and compares them with module implementation ownership. A new tracked production path therefore cannot disappear merely because it was omitted from a hand-maintained catalog.

## Public interfaces

- `scripts/check_component_coverage.py` validates Git-discovered production ownership without duplicating CMake's target graph.
- `scripts/check_documentation.py` validates module metadata, capabilities, links and the catalog-generated index; `--write-index` explicitly regenerates navigation. It does not score technical prose depth.
- `scripts/check_gap_register.py` validates an extensible issue inventory and authorization invariants. Open issues are allowed; only an explicit `--release-profile` check rejects its scoped unresolved blockers.
- `scripts/verify_build_ownership.py` validates the selected live CMake File API model against module ownership and tracked-source reachability; optional reports are generated evidence, not checked-in truth.
- Maintained workflows execute those controls on pull-request, main, and merge-group subjects as applicable.

## State and persistence

Canonical source truth is stored in versioned JSON and Markdown under `docs/` and `.github/`. CI logs and artifacts are evidence for one exact revision; they are not persistent runtime state and never authorize a Broker session.

## Failure semantics

Missing ownership, an unknown module, ambiguous longest-path ownership, an owned C/C++ source missing from the live build without an explicit `unbuilt` disposition, an omitted module document, invalid JSON, a stale capability claim, or a missing required check fails the source gate. No failure is converted into PAPER or LIVE authorization.

Capability truth uses schema v2's separate `transport_implemented`,
`advertisable`, and `authorized` fields. An implemented transport is evidence
of code only; external qualification and host controls are required before a
venue can be advertised or authorized. The control-plane validator rejects
any matrix that collapses these decisions or marks a non-current capability as
authorized.

## Security boundaries

Repository credentials, review settings, CI status, host credentials, runner custody, and Broker evidence are separate trust domains. Source verification may prove repository properties only. It cannot synthesize independent review, Merge Queue admission, host identity, PAPER account mode, or a broker-observed campaign.

## Owner-operated governance

The repository is maintained as an owner-operated project. Under that operating model, source admission should spend human effort on executable evidence rather than manufactured approval counts. `scripts/plan_owner_ruleset.py` therefore defines a read-only transition plan with the following intended server-side result:

- retain the four canonical exact-head status checks;
- retain deletion and non-fast-forward protection;
- remove required approval count, CODEOWNER approval, last-push approval and stale-review churn when there is no genuinely independent reviewer performing those roles;
- remove Merge Queue when it adds no real concurrent-integration value for the current owner-operated flow;
- keep exact-head evidence semantics through the remaining checks. Removing the `pull_request` rule also removes that Ruleset's server-enforced merge-method restriction; squash-only history is therefore not claimed by this transition unless repository merge settings are changed separately and read back.

This is **not** implemented by inventing a source-side “approval” file, dummy reviewer, duplicate CI gate or token check. Those would recreate the same formalism in another layer without adding independent judgment.

The live GitHub Ruleset is an external server fact. The 2026-09-23 readback recorded in `technical/owner-ruleset-transition.md` showed only deletion and non-fast-forward protection; the former approval/Merge Queue blocker was removed rather than kept as stale project work. Future server changes require fresh authenticated readback and must not be inferred from source-side planner output.

## Observability

Failures identify the exact path, target, module, document, invalid fact or release blocker. Successful output is intentionally small and is bound by the workflow to the exact checked-out SHA.

## Test expectations

Tests create hostile fixture repositories with newly tracked unowned components, mismatched CMake owners, and incomplete documentation indexes. The production repository is also checked directly through Git rather than an exported file list.

## Known limitations

GitHub-side review, Ruleset, Merge Queue, environment, and runner controls remain server-side facts. The source tree can describe and verify their intended interfaces but cannot force a different principal to approve or make an offline runner available.

## CI responsibility and limits

Python ownership is defined once by `scripts/run_python_tests.py`: core,
source, install and isolated process are disjoint and exhaustive partitions.
Core Runtime CI always retains native tests. Main, merge candidates and runtime,
release, policy, build, unknown or mixed-path changes also execute core/install/
process and real systemd acceptance. A PR confined to the eight explicit offline
Data/Analytics/Replay/Strategy computation/header paths or inert documentation
skips only that costly release acceptance. Source and Monitoring CI retains source
and structural checks and uses the same classifier for monitoring process scope.
`scripts/ci_change_scope.py` owns the whitelist once; complete NUL-delimited Git
diffs, base fetch/identity failures and unknown paths never grant exemptions.
Python and runtime tests cannot be waived by a CI event or client field.
Source and Monitoring CI also owns the shell-syntax and before/after
exact-index observations; the standalone Qualification Source Audit is retired. GCC and Clang sanitizers remain independent
native executions, not duplicate Python discovery.

The canonical ordinary development entry is `./scripts/dev_core.sh` plus the explicit `core` and `source` Python lanes. Install/process acceptance keeps its stronger isolated-host and release-artifact prerequisites and is invoked by the release acceptance path. An unscoped `unittest discover` command is not the project-wide acceptance interface, and targeted debugging commands do not substitute for their owning lane.

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

## Actions configuration versus executable shell checks

The existing source workflow runs digest-pinned actionlint over every complete
workflow, including dispatch-only IB qualification. The verifier itself must
reject a synthetic job-level `runner.temp` context before the lane can pass.
Runner-dependent artifact/evidence paths are step-scoped in the qualification
workflow and are validated at each consuming phase. Shell subprocess tests still
own argv binding and failure propagation; they are not an Actions expression
interpreter. This uses the existing required source lane rather than adding a
new approval gate or repeating the runtime suites.

## Shared development JSON reader

`source_json.py` supplies JSON input to documentation, component ownership,
build ownership, gap-register and owner-Ruleset planning tools. Each caller
still owns its schema and diagnostic category. The shared reader rejects
escaped duplicate keys, non-finite/overflowed numbers, nonzero underflow,
invalid UTF-8, links and changed input; its default bound is 16 MiB per file.
The bound applies before parsing, including fresh CMake File API input. An
observed numeric zero remains valid where the caller's schema permits it.

This helper assumes caller-trusted checkout/build parent directories. It is not
installed and is not reused by credentials, Broker qualification, privileged
preflight or journal readers with different trust and compatibility contracts.
The source Python partition executes its file/numeric regressions and existing
consumer tests once; no extra workflow, approval or prose-depth gate is added.
