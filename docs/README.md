# Hepta Documentation Control Plane V2

Status: current normative
Applies to: the complete active development-document graph
Verification: `python3 scripts/generate_documentation_views.py --check` and `python3 scripts/check_documentation_control_plane.py`
Authority: single active documentation index

`docs/` is the only current Hepta development-document authority. Historical versions are available through Git history only. Compatibility aliases, old PLAN/status files, archived proposals, screenshots, PDFs, explanatory legacy text and dormant build-system entrypoints are forbidden in the current tree.

Repository and package README files are entrypoints only. They must be registered by `document-registry-v2.json`, link to one canonical `docs/` target and may not create an independent product, architecture, capability or roadmap claim.

<!-- module-implementation-evidence:start -->
## Current Module Implementation Evidence

The generated module technical guides define authority, contracts and target engineering semantics. They are **not proof that every described target capability is fully implemented or deployment-qualified**. Current repository truth is the evidence state below; exact `implemented_scope`, `excluded_scope`, source paths and direct tests are authoritative in [`modules/module-registry-v2.json`](modules/module-registry-v2.json).

Registered modules: **22**. Evidence distribution: `bounded-implementation`=16, `contract-only`=1, `external-qualification-required`=1, `implemented`=2, `unsupported`=2.

| Module | Current evidence state | External qualification gates |
|---|---|---|
| `hepta.agent.support` | `bounded-implementation` | — |
| `hepta.client.runtime` | `bounded-implementation` | — |
| `hepta.documentation.control` | `implemented` | — |
| `hepta.execution.runtime` | `bounded-implementation` | `G-IB-001` |
| `hepta.feature.runtime` | `bounded-implementation` | — |
| `hepta.gateway.runtime` | `bounded-implementation` | — |
| `hepta.global.decision` | `bounded-implementation` | — |
| `hepta.management.control` | `bounded-implementation` | `G-TEAM-001` |
| `hepta.marketdata.runtime` | `bounded-implementation` | — |
| `hepta.numeric.core` | `implemented` | — |
| `hepta.observability.runtime` | `bounded-implementation` | — |
| `hepta.portfolio.compiler` | `bounded-implementation` | — |
| `hepta.protocol.contracts` | `bounded-implementation` | — |
| `hepta.research.protocol` | `contract-only` | — |
| `hepta.risk.policy` | `bounded-implementation` | — |
| `hepta.session.runtime` | `bounded-implementation` | — |
| `hepta.simulation.runtime` | `bounded-implementation` | — |
| `hepta.strategy.runtime` | `bounded-implementation` | — |
| `hepta.venue.ctp` | `unsupported` | — |
| `hepta.venue.ib` | `external-qualification-required` | `G-IB-001` |
| `hepta.venue.simulator` | `bounded-implementation` | — |
| `hepta.venue.xt` | `unsupported` | — |

`implemented` means complete only for the explicitly registered repository scope. `bounded-implementation`, `contract-only`, `unsupported`, and `external-qualification-required` retain every exclusion in the registry. No generated guide, green hosted test, directory, or build target may silently raise this ceiling.
<!-- module-implementation-evidence:end -->

## Reading order

1. [System constitution](governance/CONSTITUTION.md)
2. [Product scope](product/PRODUCT-SCOPE.md) and [capability matrix](product/CAPABILITY-MATRIX.md)
3. [Six-plane architecture](architecture/PLANE-ARCHITECTURE.md), [trust boundaries](architecture/TRUST-BOUNDARIES.md), [hot/control paths](architecture/HOT-PATH-AND-CONTROL-PATH.md), and [build/source ownership](architecture/BUILD-GRAPH-AND-SOURCE-OWNERSHIP.md)
4. [Module map](modules/MODULE-MAP.md), [ModuleManifest V3](modules/MODULE-MANIFEST-SPEC.md), and `modules/source-ownership-registry-v1.json`
5. [Contract index](contracts/CONTRACT-INDEX.md)
6. [Global roadmap](program/MASTER-ROADMAP.md), [upgrade plan](program/DOCUMENTATION-UPGRADE-PLAN.md), and [traceability model](program/TRACEABILITY-MODEL.md)
7. [Verification policy](verification/VERIFICATION-POLICY.md)

## Authority domains

- `governance/`: immutable safety, document authority and decision rights.
- `product/`: product boundary and capability ceilings.
- `architecture/`: planes, dataflow, concurrency, deployment, resources and physical ownership.
- `contracts/`: versioned inter-module interfaces and failure semantics.
- `modules/`: manifests, target/source ownership, resource budgets and extraction debt.
- `program/`: milestones, gaps, workstreams, risk and team topology.
- `verification/`: tests, faults, performance, reason codes, metrics, evidence and qualification.
- `operations/`: configuration, startup, deployment, release, incident, reconciliation and rollback.
- `research/`: point-in-time data, features, replay, validation and promotion boundaries.
- `development/`: local development, PR, contract-change, module-creation and debugging workflows.

## Machine authority

Structural truth comes from `document-registry-v2.json` and the product, contract, module, program and verification registries. The following Markdown files are deterministic generated views and must not be edited directly:

- `product/CAPABILITY-MATRIX.md`
- `contracts/CONTRACT-INDEX.md`
- `modules/MODULE-MAP.md`
- `program/MASTER-ROADMAP.md`

The marked implementation-evidence section above is a deterministic projection maintained by `scripts/generate_module_implementation_projection.py`; edit the module registry rather than the table.

Any unregistered document, entrypoint-only violation, generated drift, invalid ModuleManifest schema, unsafe source path, ambiguous physical owner, unregistered target/source compilation, unknown contract, stale dependency or historical documentation/build entry blocks the development loop.
