# Documentation Control Plane Technical Guide

Status: generated current view
Applies to: `hepta.documentation.control` version `1.1.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-documentation-control.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-documentation-control.json`](../manifests/hepta-documentation-control.json)

## Purpose and Scope

Maintains the canonical documentation, registry, generated-view and repository-integrity control plane.

This module is classified as `governance-tooling` in trust domain `development` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Enforce one registered source of truth for active documentation.
- Validate cross-registry references, generated views, module ownership and repository entrypoints.
- Reject historical aliases, stale generated views and unregistered documents.

### Non-responsibilities

- Does not claim runtime or venue qualification.
- Does not replace code tests or external evidence.
- Does not close gaps without their registered evidence.

## Trust Domain and Authority

- **Declared authority:** registries/generated views/module and build graph validators
- **Trust domain:** `development`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/documentation`
- **Backup:** `@hepta/architecture`
- **Required reviewers:** `@hepta/reliability`
- **Forbidden dependencies:** `runtime-capability`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `docs/`, `scripts/check_cmake_module_graph.py`, `scripts/check_documentation_control_plane.py`, `scripts/check_module_discipline.py`, `scripts/generate_documentation_views.py`, `scripts/hepta_document_checks.py`, `scripts/hepta_module_boundaries.py`, `scripts/hepta_registry_checks.py`
- **Build targets:** none
- **Allowed module dependencies:** none

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `hepta.metric-registry.v1`, `hepta.module-manifest.v3`, `hepta.reason-code.v1`
- **Consumes:** none

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `git-versioned`
- **persistence:** `module-declared`
- **writer:** `single-owner`

- Canonical state is the checked-in set of registries, normative documents and deterministic generators.
- Generated views carry no independent authority beyond their source registries.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `deterministic-batch`
- **shard key:** `module-declared`
- **blocking io:** `declared-only`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `validation`
- **overflow:** `typed-failure`

- Validation is deterministic over one exact repository revision.
- Failures accumulate as bounded diagnostics; partial validation success cannot override any error.

## Failure and Recovery

- **Risk-increase behavior:** `not-applicable`
- **Safe-exit behavior:** `never-weaken`

- Correct the authoritative registry or source document, regenerate views and rerun exact-head checks.
- Never repair drift by adding compatibility aliases.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Policies are checked in and reviewed as code; environment-specific relaxation is forbidden.
- Tool dependencies and schema versions are pinned by CI and repository contracts.

The manifest version is `1.1.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `documentation-control-v1`

- CI exposes validation failures, generator drift and exact revision identity.
- Diagnostics identify the offending path, registry object or relationship.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Reject links escaping the repository and unsafe manifest paths.
- Keep workflow permissions read-only except explicitly isolated administrative bootstrap operations.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `docs-generated`, `docs-control`, `module-registry`, `no-historical-docs`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Every document change passes documentation and canonical gates on the same head.
- Schema migrations must update validators, generators, tests and all registered documents atomically.

### Known gaps and qualification boundaries

- This module cannot supply independent human approval or external venue evidence.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
