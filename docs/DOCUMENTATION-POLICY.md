# Documentation control policy

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `scripts/check_documentation.py`  
Tests: `tests/python/test_documentation_control_plane.py`, `tests/python/test_documentation_structure.py`

## Required module contract

Every entry in `docs/module-catalog.json` must have:

1. a unique module ID;
2. an allowed lifecycle status;
3. one existing module document;
4. one or more existing implementation paths;
5. tests for every `CURRENT` or `QUALIFICATION_REQUIRED` module;
6. an explicit broker-mutation classification;
7. an explicit production-authorization value.

## Structure is not technical completeness

The automated gate checks metadata, file existence, references, lifecycle and
capability consistency. It does not grade technical depth. There are no word,
byte, paragraph or keyword quotas: padding prose cannot prove a correct design.
A structural PASS is not an endorsement of the module's implementation or tests.

For a maintained module, engineering review must be able to locate its public
inputs/outputs, state transitions, concurrency ownership, failure/retry behavior,
persistent format and upgrade rules, and the executable tests for those claims.
These can live in linked technical references; do not duplicate one template
per source file. Experimental modules document their actual boundary and missing
behavior rather than inventing contracts for nonexistent implementations.

## Truthfulness rules

- `EXPERIMENTAL` and `PROPOSAL` modules may not be production-authorized.
- `UNAVAILABLE` capabilities may not have a mutation path.
- CTP and XT/QMT must remain fail-closed until a real transport, authoritative callbacks, reconciliation, and venue-specific qualification exist.
- Repository source may describe an IB PAPER qualification path but may not claim that external runners, credentials, Broker sessions, host policy or broker-observed receipts exist merely because source files or CI jobs exist.
- Repository review/ruleset state is an engineering admission fact, not Broker authority.
- A document may not call a file “installed” unless the canonical install process actually installs it.
- CURRENT documentation must not depend on a developer-specific absolute path.

## Change discipline

A pull request that changes a module implementation, public protocol, persistent schema, reason code, service unit, capability state, release contract or Broker qualification scenario must update the corresponding module/technical documentation and tests in the same pull request.

`documentation-control-plane-exact-head` validates catalog ownership, links, lifecycle/capability truth and documentation structure. `scripts/run_python_tests.py` assigns disjoint source, core and install test-file sets. The sanitizer compiler variants remain independent behavioral evidence.
