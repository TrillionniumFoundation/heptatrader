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

## State and persistence

Canonical source truth is stored in versioned JSON and Markdown under `docs/` and `.github/`. CI logs and artifacts are evidence for one exact revision; they are not persistent runtime state and never authorize a Broker session.

## Failure semantics

Missing ownership, an unknown module, ambiguous longest-path ownership, build-inventory owner drift, an omitted module document, invalid JSON, a stale capability claim, or a missing required check fails the source gate. No failure is converted into PAPER or LIVE authorization.

## Security boundaries

Repository credentials, review settings, CI status, host credentials, runner custody, and Broker evidence are separate trust domains. Source verification may prove repository properties only. It cannot synthesize independent review, Merge Queue admission, host identity, PAPER account mode, or a broker-observed campaign.

## Observability

Failures identify the exact path, target, module, document, or contract token that drifted. Successful output is intentionally small and is bound by the workflow to the exact checked-out SHA.

## Test expectations

Tests create hostile fixture repositories with newly tracked unowned components, mismatched CMake owners, and incomplete documentation indexes. The production repository is also checked directly through Git rather than an exported file list.

## Known limitations

GitHub-side review, Ruleset, Merge Queue, environment, and runner controls remain server-side facts. The source tree can describe and verify their intended interfaces but cannot force a different principal to approve or make an offline runner available.
