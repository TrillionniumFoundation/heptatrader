# Hepta Documentation Control Plane V2

Status: current normative
Applies to: the complete active development-document graph
Verification: `python3 scripts/generate_documentation_views.py --check` and `python3 scripts/check_documentation_control_plane.py`
Authority: single active documentation index

`docs/` is the only current Hepta development-document authority. Historical versions are available through Git history only. Compatibility aliases, old PLAN/status files, archived proposals, screenshots, PDFs, explanatory legacy text and dormant build-system entrypoints are forbidden in the current tree.

Repository and package README files are entrypoints only. They must be registered by `document-registry-v2.json`, link to one canonical `docs/` target and may not create an independent product, architecture, capability or roadmap claim.

## Reading order

1. [System constitution](governance/CONSTITUTION.md)
2. [Product scope](product/PRODUCT-SCOPE.md) and [capability matrix](product/CAPABILITY-MATRIX.md)
3. [Six-plane architecture](architecture/PLANE-ARCHITECTURE.md), [trust boundaries](architecture/TRUST-BOUNDARIES.md), [hot/control paths](architecture/HOT-PATH-AND-CONTROL-PATH.md), and [build/source ownership](architecture/BUILD-GRAPH-AND-SOURCE-OWNERSHIP.md)
4. [Module map](modules/MODULE-MAP.md), [ModuleManifest V2](modules/MODULE-MANIFEST-SPEC.md), and `modules/source-ownership-registry-v1.json`
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

Any unregistered document, entrypoint-only violation, generated drift, invalid ModuleManifest schema, unsafe source path, ambiguous physical owner, unregistered target/source compilation, unknown contract, stale dependency or historical documentation/build entry blocks the development loop.
