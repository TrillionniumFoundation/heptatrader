# Session Runtime Technical Guide

Status: generated current view
Applies to: `hepta.session.runtime` version `1.1.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-session-runtime.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-session-runtime.json`](../manifests/hepta-session-runtime.json)

## Current Implementation Evidence

- **Evidence state:** `bounded-implementation`
- **Resource guardrail profile:** `guardrail-1`
- **External qualification gates:** none

### Implemented repository scope

- durable lease authority
- epoch and fencing semantics
- atomic lease-store migration
- session-supervisor contract

### Excluded or not-current scope

- distributed consensus-backed session authority
- multi-region high availability

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/tool_host/`
- **Test evidence:** `tests/session_supervisor_protocol_boundary_tests.cpp`, `tests/session_supervisor_lease_store_migration_tests.cpp`, `tests/unix_session_supervisor_server_tests.cpp`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Owns session identity, leases, epochs, fencing and supervisor lifecycle used to prevent stale actors from exercising capabilities.

This module is classified as `stateful-library` in trust domain `agent-gateway` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Issue and validate bounded session/lease identity.
- Advance epochs and fence superseded owners deterministically.
- Coordinate startup, heartbeat, expiry and supervised restart state.

### Non-responsibilities

- Does not decide trades or mutate venue orders.
- Does not treat network liveness as authority by itself.
- Does not reuse expired capability across epochs.

## Trust Domain and Authority

- **Declared authority:** durable session lease and supervisor protocol state
- **Trust domain:** `agent-gateway`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/session-control`
- **Backup:** `@hepta/gateway`
- **Required reviewers:** `@hepta/security-runtime`
- **Forbidden dependencies:** `hepta.venue.*`, `broker.credentials`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/tool_host/session_supervisor_lease_`, `HeptaTrade/tool_host/session_supervisor_protocol`
- **Build targets:** `hepta_session_core`
- **Allowed module dependencies:** none

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `hepta.session-supervisor.v1`
- **Consumes:** none

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `durable-generation-fenced`
- **persistence:** `encrypted-lease-store-atomic-replace`
- **writer:** `single-owner`

- Session state is authoritative for lease/epoch ownership and expiry.
- Dependent cached capabilities are invalidated whenever the session epoch changes.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `supervisor-serialized`
- **shard key:** `supervisor-single-owner`
- **blocking io:** `durable-store-and-af-unix-control-only`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `control-path`
- **overflow:** `typed-failure`

- Serialize lease transitions and reject stale heartbeats/owners.
- Bound supervisor event queues and prioritize fencing/expiry over new admission.

## Failure and Recovery

- **Risk-increase behavior:** `fail-closed`
- **Safe-exit behavior:** `never-weaken`

- On restart, establish a new epoch or prove continuation according to contract before serving work.
- Fence uncertain prior owners before reissuing authority.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Lease duration, heartbeat cadence and restart policy are canonical configuration.
- Unsafe timeout relaxation requires reviewed contract and qualification changes.

The manifest version is `1.1.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `session-control-v1`

- Track active sessions, epoch changes, heartbeat lag, expiries, fencing and restart loops.
- Correlate without exposing secret token material.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Capabilities are scoped to session identity, audience and lifetime.
- Prevent confused-deputy use across accounts, agents or environments.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `module-documentation-coverage`, `protocol-contracts`, `session-boundary`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Start Session before dependent control/data-plane readiness.
- On instability, stop admission and fence rather than extending leases optimistically.

### Known gaps and qualification boundaries

- Distributed session authority requires a deployed consensus/storage design and evidence if moved beyond the current boundary.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
