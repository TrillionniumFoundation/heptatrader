# Documentation control policy

Status: CURRENT
Applies to: repository HEAD
Implementation: `scripts/check_documentation.py`
Tests: `tests/python/test_documentation_control_plane.py`, `tests/python/test_documentation_depth.py`

## Required module contract

Every entry in `docs/module-catalog.json` must have:

1. a unique module ID;
2. an allowed lifecycle status;
3. one existing module document;
4. one or more existing implementation paths;
5. tests for every `CURRENT` or `QUALIFICATION_REQUIRED` module;
6. an explicit broker-mutation classification;
7. an explicit production-authorization value.

A module document is not considered complete merely because it exists or contains a technical heading. Maintained module documentation must contain substantive prose and cover the engineering topics needed to operate and change the component: purpose/responsibility, public contracts, state/persistence/recovery where applicable, failure semantics, security/authority boundaries, observability, executable testing, operations and limitations. Module-specific wording is allowed; topic checks provide editorial advice rather than forcing one ceremonial template.

Prose byte length, heading vocabulary, topic counts and paragraph counts are
advisory only. They cannot prove engineering completeness and cannot block a
correct change. Missing documents, broken links, missing implementation/tests,
ambiguous module ownership and false capability claims remain hard failures.
Review changes against the actual protocol, state machine, failure behavior and
executable tests; do not add padding solely to satisfy a documentation script.

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

`documentation-control-plane-exact-head` validates catalog ownership, links, lifecycle/capability truth with editorial depth advice. It should not duplicate the complete runtime/release behavioral suite; those assertions belong to the behavior-bearing core/release gates.
