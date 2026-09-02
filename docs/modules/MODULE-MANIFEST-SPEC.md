# ModuleManifest V3 Specification

Status: current normative
Applies to: all current, experimental, planned, unsupported and deprecated modules
Verification: Draft 2020-12 validation, source ownership validation, configured CMake File API graph validation, and implementation-evidence validation
Authority: module manifest contract

A module is an authority, state, failure and concurrency boundary—not merely a directory. Every module manifest is validated against `module-manifest-schema-v3.json` before semantic checks run. A syntactically valid manifest is necessary but is not sufficient evidence that every capability named by the module exists in the repository.

## Required identity and lifecycle

Each manifest declares:

```text
schema = heptatrader.module-manifest.v3
stable module id
semantic version
lifecycle
kind and trust domain
authority statement
exclusive or shared-migration ownership
```

`shared-migration` requires an open `migration_gap`. An exclusive module must not carry a migration gap. Unknown fields, malformed nested objects, duplicate arrays, unsafe paths and invalid lifecycle/ownership values are rejected by the schema.

`lifecycle = current` means that the registered module boundary participates in the current repository graph. It does not mean that every possible production capability suggested by the module name is implemented, externally qualified or deployable. Capability maturity is controlled separately by the implementation-evidence ledger in `module-registry-v2.json`.

## Required engineering contracts

Every manifest declares:

```text
source roots and CMake targets
provided and consumed contract IDs
allowed and forbidden module dependencies
state model / persistence / writer
concurrency / shard / blocking-I/O / cross-module-lock policy
backpressure and overflow behavior
risk-increase and safe-exit failure behavior
resource budget class
DRI / backup / cross-domain reviewers
verification check IDs
technical-guide path and complete coverage-topic set
```

All contract, module and verification IDs must resolve to canonical registries. Current and experimental CMake targets must exist in the configured or explicitly optional profile.

A manifest field is a boundary requirement, not self-authenticating implementation evidence. For example, a persistence value describing an approved checkpoint does not prove that an artifact loader, checkpoint writer, corruption recovery path or migration implementation exists. Those claims must be backed by explicit source and test paths in the implementation-evidence ledger.

## Physical source ownership

Manifest claims are not the physical ownership authority. The exact owner of every active C/C++ file is defined in `source-ownership-registry-v1.json`. Multiple manifest claims are permitted only through a bounded exception whose participant set exactly matches the observed owners and whose physical owner, gap, milestone and exit condition are explicit.

The configured CMake graph is read through the File API. A source compiled by a target owned by another module requires an exact `(target, source)` migration exception. Tests that directly compile production sources are governed by the same rule. No wildcard exception or “any owner marked shared” shortcut is allowed.

## Implementation-evidence ledger

`module-registry-v2.json` contains exactly one implementation-evidence entry for every manifest. The ledger is validated by `scripts/check_module_implementation_evidence.py` and is part of the documentation control plane. It separates six repository states:

| State | Meaning |
|---|---|
| `implemented` | The complete declared repository scope has direct source and negative/positive test evidence, with no hidden excluded scope. |
| `bounded-implementation` | A useful runtime boundary exists, but the ledger names material capabilities that are outside the repository implementation. |
| `contract-only` | The repository implements a data, validation or protocol contract, not the full runtime service suggested by the broader architectural name. |
| `harness-only` | The repository implements a deterministic test or orchestration harness, not a production runtime. |
| `unsupported` | The module is a fail-closed scaffold used to reject activation and to exercise negative tests. |
| `external-qualification-required` | Repository implementation exists, but operational use remains forbidden until an external environment produces an exact-artifact qualification receipt. |

Every entry contains:

```text
module id
truthful implementation state
implemented repository scope
explicit excluded scope
source evidence paths
test evidence paths
external gate IDs
numeric repository planning guardrail
```

The checker rejects missing modules, duplicate entries, unsafe paths, absent evidence, unregistered external gates, hidden exclusions on an `implemented` state, and capability-state inflation above a registered truth floor.

The ledger is deliberately stricter for architectural names that can be mistaken for complete services:

- `hepta.management.control` is bounded to the in-process lifecycle authority unless durable configuration, rollout persistence and distributed reconciliation source and tests are added.
- `hepta.strategy.runtime` is contract-only while the source root contains proposal construction and validation but no approved artifact loader or OS-level sandbox.
- `hepta.simulation.runtime` is harness-only while it provides deterministic multi-agent allocation composition but no virtual clock or scenario runtime.
- `hepta.research.protocol` is contract-only while it provides validation protocol and fixtures rather than a production point-in-time data platform.
- CTP and XT remain unsupported.
- IB remains externally gated until exact official-SDK, host, account and broker-observed PAPER evidence exists.

Changing one of these truth-floor states requires the implementing source, direct tests, operational evidence where applicable, and the ledger update in the same revision.

## Resource guardrails

The manifest `resource_budget` value is a stable budget-class identifier. Each implementation-evidence entry resolves that class to a numeric repository planning guardrail covering:

```text
maximum threads
maximum queued items and bytes
maximum memory
maximum file descriptors
deadline
maximum telemetry series
restart burst
```

These values prevent an unbounded phrase from masquerading as a budget. Their scope is explicitly `repository-planning-ceiling-not-target-host-slo`. They are not a target-host latency promise and are not proof that an operating-system quota is enforced.

The `enforcement` field is also truthful:

- `test-checked` means a repository test directly exercises the relevant bounded behavior.
- `negative-test-only` is reserved for unsupported adapters.
- `documentation-ceiling` means the numeric ceiling is a planning and review contract; runtime quota enforcement has not been inferred.

Promotion to runtime-enforced status requires executable admission or operating-system controls and fault tests in the same revision. A documentation ceiling must never be cited as PAPER, LIVE or deployment qualification.

## Completion rule

A module boundary is repository-complete only when:

1. the manifest passes the formal schema;
2. all source files have one physical owner;
3. the configured target/source/dependency graph matches the registries;
4. no unregistered cross-module compilation remains;
5. all migration exceptions linked to the module are removed or remain attached to one open gap;
6. the module has one unique generated technical guide covering every required engineering topic;
7. documentation profiles, manifests, generated guides and canonical registries are byte-consistent;
8. the implementation-evidence state matches direct source and test paths;
9. material excluded scope is explicit rather than implied away;
10. resource guardrails resolve to positive numeric ceilings with an honest enforcement state;
11. module-local, documentation and system verification pass on the same unchanged revision.

Repository completion does not imply independent review, merge-group verification, merge to the default branch, artifact publication, external qualification, deployment, PAPER authorization or LIVE authorization.

## Concrete engineering semantics

Current, experimental and unsupported modules must state concrete engineering behavior. `state.persistence` identifies the required durable, derived, checkpoint or no-persistence model; `concurrency.shard_key` identifies the serialization or ownership key, including an explicit `none`; `concurrency.blocking_io` identifies the exact boundary where blocking I/O is allowed or states that it is forbidden.

Generic values such as `module-declared` and `declared-only` are invalid. A non-empty placeholder is not an engineering contract. Unsupported modules still declare concrete `none-unsupported` and `forbidden-unsupported` behavior so activation, packaging and future implementation cannot inherit a permissive default.

Concrete wording must not overstate the source tree. When a manifest describes a target boundary whose implementation is partial, the implementation-evidence ledger is the controlling truth source for what exists now. A future-capability sentence belongs in excluded scope or an accepted engineering obligation, not in present-tense operational claims.

## Detailed development design requirements

A module may not be promoted from `contract-only`, `harness-only` or `bounded-implementation` solely by increasing generated-guide length. Promotion requires implementation-ready evidence for the new scope:

1. field-level contract or IDL, including errors, idempotency and compatibility;
2. state transition table with events, guards, effects and terminal states;
3. data ownership, persistence layout, atomicity and migration behavior;
4. thread ownership, queue bounds, lock order and deadline propagation;
5. failure injection covering timeout, corruption, restart, duplicate and stale inputs;
6. observability with bounded labels and reason codes;
7. deployment, upgrade, rollback and recovery procedure;
8. direct positive and negative tests;
9. exact-revision evidence and, for external systems, environment qualification.

Generated technical guides remain canonical summaries and indexes. They do not replace source-linked state machines, schemas, fault tests or external qualification receipts.

## External gates

External gates are fail-closed. The checker cross-references the implementation ledger with `gap-registry-v2.json` and rejects a closed external gate unless a separate machine-readable qualification receipt is introduced and validated.

`G-IB-001` requires real broker-environment evidence. `G-TEAM-001` requires live organization teams, CODEOWNERS mapping and effective ruleset readback. Hosted CI, local fixtures, self-review, administrator bypass and narrative comments are not substitutes for either gate.
