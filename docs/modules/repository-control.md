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
- `scripts/check_documentation.py` validates module metadata, capabilities, and documentation links.
- `scripts/check_gap_register.py` validates supported-scope gap evidence and authorization invariants.
- `scripts/verify_build_ownership.py` compares fresh CMake File API output with `docs/build-targets.json`.
- Maintained workflows execute those controls on pull-request, main, and merge-group subjects as applicable.

## CI responsibility model

Each expensive evidence family has one behavior-bearing owner:

- **Core Runtime CI** owns the canonical core build, CTest behavior, Python regression corpus, install inventory, package/preflight and release simulator lifecycle smoke.
- **Documentation Control Plane** owns source/document/module/build-model truth only. It does not rerun the full Python behavior corpus already owned by Core Runtime CI.
- **Periodic Runtime Resilience** owns GCC and Clang ASan/UBSan resilience. It runs on a daily schedule and on pull requests that touch execution/risk/OMS/state/reconciliation/IB/simulator/test/build paths rather than on every repository change.
- **Qualification Source Audit** owns only the owner-operated IB PAPER artifact/rollout/certification trust boundary and is path-scoped to that surface.

The active GitHub ruleset still names historical `canonical-full-suite-*` and `exact-merge-candidate` contexts. Until server-side ruleset administration removes those names, the corresponding workflow jobs are compatibility-only context emitters. They must not rebuild, retest, reinstall dependencies or duplicate source validators. Keeping the context name is temporary migration compatibility, not evidence duplication.

## State and persistence

Canonical source truth is stored in versioned JSON and Markdown under `docs/` and `.github/`. CI logs and artifacts are evidence for one exact revision; they are not persistent runtime state and never authorize a Broker session.

## Failure semantics

Missing ownership, an unknown module, ambiguous longest-path ownership, build-inventory owner drift, an omitted module document, invalid JSON, a stale capability claim, a missing behavior-bearing owner, or a required server-side compatibility context failing to emit fails the applicable source gate. No failure is converted into PAPER or LIVE authorization.

A compatibility context must never conceal failure of its behavior-bearing owner. The required Core Runtime and Documentation checks remain independent server-side contexts; sanitizer resilience is non-required observation evidence unless the ruleset is explicitly changed.

## Security boundaries

Repository credentials, review settings, CI status, host credentials, runner custody, and Broker evidence are separate trust domains. Source verification may prove repository properties only. It cannot synthesize independent review, Merge Queue admission, host identity, PAPER account mode, or a broker-observed campaign.

Removing duplicate CI execution does not relax Execution, Risk or Journal runtime invariants. Journal-before-send, stable mutation identity, uncertainty handling, risk gates, owner fencing and reconciliation are behavior-bearing runtime properties and continue to be tested by their owning suites.

## Observability

Failures identify the exact path, target, module, document, contract token or CI responsibility that drifted. Successful source-control output is intentionally small and bound to the exact checked-out SHA. Periodic resilience results remain separately visible rather than inflating every merge gate.

## Test expectations

Tests create hostile fixture repositories with newly tracked unowned components, mismatched CMake owners, incomplete documentation indexes and duplicated/missing workflow responsibilities. The production repository is also checked directly through Git rather than an exported file list.

CI-role regressions should prove that a compatibility workflow cannot silently regain core build, full Python, package/release-smoke or source-truth responsibilities, and that the dedicated resilience workflow retains both sanitizer compiler lanes.

## Known limitations

GitHub-side review, Ruleset, Merge Queue, environment and runner controls remain server-side facts. The source tree can describe and verify their intended interfaces but cannot mutate a ruleset through the currently installed GitHub connection. Consequently the legacy required context names remain as inert compatibility emitters until an administrator updates ruleset `heptatrader-main-governance-v1`; deleting them from source first would deadlock Merge Queue without increasing safety.
