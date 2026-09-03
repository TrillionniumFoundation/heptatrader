# Feature Runtime Technical Guide

Status: generated current view
Applies to: `hepta.feature.runtime` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-feature-runtime.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-feature-runtime.json`](../manifests/hepta-feature-runtime.json)

## Current Implementation Evidence

- **Evidence state:** `bounded-implementation`
- **Resource guardrail profile:** `guardrail-1`
- **External qualification gates:** none

### Implemented repository scope

- authority-bound sharded mid/spread feature snapshots
- deterministic feature digest and freshness checks
- bounded fixed-catalog feature DAG validation
- transactional rolling-mean state with cycle and overflow rejection

### Excluded or not-current scope

- arbitrary feature plugin execution
- persistent rolling-window recovery
- cache eviction policy
- broad offline/online feature parity

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/features/`
- **Test evidence:** `tests/feature_generation_tests.cpp`, `tests/python/test_bounded_runtime_components.py`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Transforms authoritative point-in-time market snapshots into deterministic, versioned feature snapshots for downstream decision logic.

This module is classified as `data-plane` in trust domain `feature` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Compute registered feature sets using fixed-point and point-in-time inputs.
- Bind outputs to input digest, epoch, sequence and generation lineage.
- Reject stale, gapped, malformed or unauthorized market-data inputs.

### Non-responsibilities

- Does not ingest raw venue feeds or repair sequence gaps.
- Does not place orders or grant strategy authority.
- Does not accept mutable diagnostic snapshots as proof of Market Data authority.

## Trust Domain and Authority

- **Declared authority:** deterministic feature generations
- **Trust domain:** `feature`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/data-platform`
- **Backup:** `@hepta/research-validation`
- **Required reviewers:** `@hepta/architecture`
- **Forbidden dependencies:** `hepta.execution.runtime`, `hepta.venue.*`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/features/`
- **Build targets:** `hepta_feature_runtime`
- **Allowed module dependencies:** `hepta.marketdata.runtime`, `hepta.numeric.core`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `feature-snapshot.v1`
- **Consumes:** `market-event.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `immutable-generation`
- **persistence:** `derived-process-local-generation-cache`
- **writer:** `single-owner`

- Feature state is keyed by market identity and feature-set version with monotonic generation.
- Outputs are derived and reproducible; source lineage is retained in every snapshot.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `instrument-sharded`
- **shard key:** `venue-instrument-feature-set`
- **blocking io:** `forbidden-on-feature-compute`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `bounded-coalescing`
- **overflow:** `typed-failure`

- Process only store-issued risk-ready receipts and reject input regression/conflict.
- Use bounded per-key storage and typed capacity/overflow failures.

## Failure and Recovery

- **Risk-increase behavior:** `stale-reject`
- **Safe-exit behavior:** `never-weaken`

- Recompute from current authoritative snapshots after restart or cache loss.
- Invalidate outputs when source lineage, feature version or freshness changes.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Feature-set IDs and numeric policies are versioned contracts.
- Unknown feature versions fail closed rather than falling back silently.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `feature-v1`

- Report compute latency, stale/gap rejections, numeric failures, cache generation and capacity.
- Avoid unbounded instrument labels beyond registered identities.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Treat Feature as a pure derivation boundary without credentials or execution dependencies.
- Construction-restricted receipts are same-process capabilities, not network authentication.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `feature-determinism`, `feature-generation`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Canary new feature versions in replay/simulator before promotion.
- Rollback selects the prior registered feature-set version and invalidates incompatible cached outputs.

### Known gaps and qualification boundaries

- Cross-process Market Data to Feature deployment would require an authenticated authority envelope not supplied by the same-process receipt.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
