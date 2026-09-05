# Numeric Core Technical Guide

Status: generated current view
Applies to: `hepta.numeric.core` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-numeric-core.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-numeric-core.json`](../manifests/hepta-numeric-core.json)

## Current Implementation Evidence

- **Evidence state:** `implemented`
- **Resource guardrail profile:** `guardrail-6`
- **External qualification gates:** none

### Implemented repository scope

- checked fixed-point decimal arithmetic
- trusted-boundary finite/range validation
- canonical numeric projection

### Excluded or not-current scope

- None within the explicitly registered repository scope.

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/numeric/`
- **Test evidence:** `tests/fixed_decimal_tests.cpp`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Defines deterministic fixed-point numeric types, checked arithmetic and venue-conversion boundaries used by risk-sensitive code.

This module is classified as `contract-library` in trust domain `shared-trusted` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Provide canonical parsing, formatting and checked raw construction.
- Detect overflow, scale mismatch and lossy floating-point conversion.
- Supply deterministic arithmetic primitives shared by contracts and solvers.

### Non-responsibilities

- Does not select business rounding policy implicitly.
- Does not accept NaN, infinity or unchecked binary-floating values on risk paths.
- Does not emulate vendor decimal ABIs without verified equivalence.

## Trust Domain and Authority

- **Declared authority:** fixed numeric boundary
- **Trust domain:** `shared-trusted`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/contracts`
- **Backup:** `@hepta/risk`
- **Required reviewers:** `@hepta/architecture`
- **Forbidden dependencies:** `*`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/numeric/`
- **Build targets:** `hepta_numeric_core`
- **Allowed module dependencies:** none

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `hepta.numeric.fixed-v1`
- **Consumes:** none

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `none`
- **persistence:** `none`
- **writer:** `single-owner`

- Numeric values are immutable value types with explicit validity and scale.
- No module-local persistent state is required.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `pure-reentrant`
- **shard key:** `none`
- **blocking io:** `forbidden`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `caller-bounded`
- **overflow:** `typed-failure`

- Operations are synchronous and deterministic; there is no internal queue.
- Callers handle typed arithmetic failure before state mutation.

## Failure and Recovery

- **Risk-increase behavior:** `fail-closed`
- **Safe-exit behavior:** `never-weaken`

- Numeric failure is not retried with a less precise representation.
- Correct the input, scale or policy and recompute from authoritative state.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Scale, range and rounding are versioned policies, not runtime guesses.
- Venue conversions require explicit adapter rules and golden vectors.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `numeric-core-v1`

- Count overflow, parse, scale and lossy-conversion failures at call boundaries.
- Do not emit sensitive full payloads solely for numeric diagnostics.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Reject malformed representations that could bypass limits or comparisons.
- Keep all risk calculations on checked deterministic paths.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `numeric-negative`, `numeric-properties`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Any numeric policy change requires full replay, cross-compiler and schema compatibility testing.
- Rollback restores the prior versioned numeric policy and invalidates incompatible artifacts.

### Known gaps and qualification boundaries

- IB Decimal interoperability is not qualified until the official SDK/DFP ABI and golden vectors are verified.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
