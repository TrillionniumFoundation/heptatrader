# Documentation control policy

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `scripts/check_documentation.py`  
Tests: `tests/python/test_documentation_control_plane.py`

## Required module contract

Every entry in `docs/module-catalog.json` must have:

1. a unique module ID;
2. an allowed lifecycle status;
3. one existing module document;
4. one or more existing implementation paths;
5. tests for every `CURRENT` or `QUALIFICATION_REQUIRED` module;
6. an explicit broker-mutation classification;
7. an explicit production-authorization value.

A module document must state its status, implementation paths, tests, responsibilities, public contracts, state and persistence, failure semantics, security boundaries, observability, and known limitations.

## Truthfulness rules

- `EXPERIMENTAL` and `PROPOSAL` modules may not be production-authorized.
- `UNAVAILABLE` capabilities may not have a mutation path.
- CTP and XT/QMT must remain fail-closed until a real transport, authoritative callbacks, reconciliation, and venue-specific qualification exist.
- Repository source may describe an IB PAPER qualification path but may not claim that organization teams, rulesets, environments, runners, credentials, broker sessions, or receipts exist.
- A document may not call a file “installed” unless the canonical install process actually installs it.
- CURRENT documentation must not depend on a developer-specific absolute path.

## Change discipline

A pull request that changes a module implementation, public protocol, persistent schema, reason code, service unit, or capability state must update its module document and tests in the same pull request. `documentation-control-plane-exact-head` validates the catalog, links, statuses, and high-risk capability invariants.
