# Management Control Plane Technical Guide

Status: generated current view
Applies to: `hepta.management.control` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-management-control.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-management-control.json`](../manifests/hepta-management-control.json)

## Current Implementation Evidence

- **Evidence state:** `bounded-implementation`
- **Resource guardrail profile:** `guardrail-1`
- **External qualification gates:** `G-TEAM-001`

### Implemented repository scope

- in-process module lifecycle authority
- health, quarantine and rollback state transitions
- checksummed local desired-state rollout persistence
- atomic restart recovery and deterministic reconciliation actions

### Excluded or not-current scope

- distributed rollout fan-out executor
- multi-writer consensus or remote configuration service
- high-availability management authority

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/management/`
- **Test evidence:** `tests/module_lifecycle_tests.cpp`, `tests/python/test_bounded_runtime_components.py`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Coordinates governed lifecycle, rollout, configuration and operational control without obtaining broker mutation authority.

This module is classified as `control-plane` in trust domain `management` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Apply declared lifecycle transitions and rollout policies.
- Distribute canonical configuration revisions and operational commands.
- Coordinate disable, drain, rollback and incident controls across modules.

### Non-responsibilities

- Does not submit venue orders or override Execution risk gates.
- Does not generate strategy proposals or rewrite authoritative journals.
- Does not turn an experimental capability into a qualified one by configuration alone.

## Trust Domain and Authority

- **Declared authority:** module/config/resource lifecycle
- **Trust domain:** `management`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/platform`
- **Backup:** `@hepta/architecture`
- **Required reviewers:** `@hepta/security-runtime`, `@hepta/operations`
- **Forbidden dependencies:** `hepta.venue.*`, `broker.credentials`, `hepta.execution.runtime`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/management/`
- **Build targets:** `hepta_management_control`
- **Allowed module dependencies:** `hepta.protocol.contracts`, `hepta.observability.runtime`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `capital-policy.v1`, `hepta.configuration-authority.v1`, `hepta.module-lifecycle.v1`
- **Consumes:** `hepta.module-manifest.v3`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `versioned-control`
- **persistence:** `canonical-config-plus-versioned-rollout-state`
- **writer:** `single-owner`

- Management state is desired-state and rollout metadata; runtime modules retain their domain state.
- Every control change is revisioned and auditable.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `control-domain-sharded`
- **shard key:** `module-rollout-domain`
- **blocking io:** `control-path-only`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `bounded-control`
- **overflow:** `typed-failure`

- Serialize conflicting lifecycle/configuration updates and reject stale revisions.
- Bound fan-out queues and surface partial delivery for reconciliation.

## Failure and Recovery

- **Risk-increase behavior:** `cannot-authorize`
- **Safe-exit behavior:** `never-weaken`

- Reconcile desired state with observed module state after restart.
- On uncertain rollout, freeze expansion and drive components to the last safe revision.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Management consumes the canonical configuration authority and may narrow, not silently broaden, capability.
- Emergency controls are explicit and auditable.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `management-v1`

- Track rollout phase, revision adoption, rejected transitions, drain progress and rollback status.
- Keep operator identity and reason code in audit events.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Separate operational control roles from execution credentials.
- All privileged commands require authenticated capability and immutable audit identity.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `lifecycle-faults`, `rollout-rollback`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Use staged rollout with health gates and automatic stop conditions.
- Incident rollback favors risk reduction and may leave capabilities disabled pending review.

### Known gaps and qualification boundaries

- Actual team separation and independent approval remain external governance requirements.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
