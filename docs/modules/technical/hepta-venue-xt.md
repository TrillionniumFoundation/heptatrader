# XT Venue Adapter Technical Guide

Status: generated current view
Applies to: `hepta.venue.xt` version `1.0.0` (unsupported)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-venue-xt.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-venue-xt.json`](../manifests/hepta-venue-xt.json)

## Purpose and Scope

Defines the quarantined adapter boundary for a future XT integration; the current lifecycle does not authorize runtime use.

This module is classified as `unsupported-adapter` in trust domain `execution-authority` with lifecycle `unsupported`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Keep vendor-specific transport and normalization behind the venue adapter boundary.
- Document required contracts, dependencies and enablement prerequisites.
- Fail closed while unsupported.

### Non-responsibilities

- Does not expose an active XT connection or credentials.
- Does not bypass Execution, Risk or Session.
- Does not claim PAPER/LIVE compatibility.

## Trust Domain and Authority

- **Declared authority:** none
- **Trust domain:** `execution-authority`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/venue-xt`
- **Backup:** `@hepta/execution-core`
- **Required reviewers:** `@hepta/security-runtime`
- **Forbidden dependencies:** `broker.transport`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/adapter_xt/`
- **Build targets:** `hepta_venue_xt`
- **Allowed module dependencies:** `hepta.protocol.contracts`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `hepta.venue.v1`
- **Consumes:** none

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `none`
- **persistence:** `module-declared`
- **writer:** `single-owner`

- No authoritative runtime state may be enabled while lifecycle is unsupported.
- Future order/session state must map to canonical Execution and reconciliation contracts.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `none`
- **shard key:** `module-declared`
- **blocking io:** `declared-only`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `none`
- **overflow:** `typed-failure`

- A future adapter must preserve vendor sequence/order identity and bounded callbacks.
- Current unsupported entrypoints reject activation.

## Failure and Recovery

- **Risk-increase behavior:** `always-reject`
- **Safe-exit behavior:** `never-weaken`

- No runtime recovery claim exists while unsupported.
- Future recovery must reconnect, fence stale sessions and reconcile before mutation.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Enablement remains false unless lifecycle, capability and qualification registries are changed together.
- Vendor SDK/version/configuration must be pinned when introduced.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `unsupported-v1`

- Unsupported activation attempts must be visible.
- Future adapter telemetry must include session, sequence, callback and reconciliation health.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- No vendor credential may enter unsupported code paths.
- Vendor code remains isolated from strategy and management boundaries.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `unsupported-venue-negative`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Do not deploy or package as an enabled venue.
- Activation requires reviewed code, negative tests, simulator/PAPER evidence and rollback procedures.

### Known gaps and qualification boundaries

- The adapter is intentionally unsupported; all real integration and qualification work remains future scope.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
