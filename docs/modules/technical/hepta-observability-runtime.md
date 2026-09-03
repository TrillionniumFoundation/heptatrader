# Observability Runtime Technical Guide

Status: generated current view
Applies to: `hepta.observability.runtime` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-observability-runtime.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-observability-runtime.json`](../manifests/hepta-observability-runtime.json)

## Current Implementation Evidence

- **Evidence state:** `bounded-implementation`
- **Resource guardrail profile:** `guardrail-1`
- **External qualification gates:** none

### Implemented repository scope

- low-cardinality metric registry
- bounded series registry
- runtime counters and latency distributions

### Excluded or not-current scope

- full module-wide telemetry coverage
- production telemetry backend qualification

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/observability/`
- **Test evidence:** `tests/runtime_telemetry_tests.cpp`, `tests/python/test_metric_registry.py`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Collects bounded metrics, logs and traces needed to operate and verify the system without becoming a trading authority.

This module is classified as `support-library` in trust domain `shared-trusted` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Expose registered metrics and reason-code dimensions.
- Preserve correlation across sessions, decisions, orders and reconciliations.
- Apply bounded buffering, sampling and redaction.

### Non-responsibilities

- Does not mutate domain state or authorize retries.
- Does not retain secrets or unrestricted payload copies.
- Does not let telemetry failure block required risk reduction.

## Trust Domain and Authority

- **Declared authority:** bounded telemetry
- **Trust domain:** `shared-trusted`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/reliability`
- **Backup:** `@hepta/platform`
- **Required reviewers:** `@hepta/security-runtime`
- **Forbidden dependencies:** `broker.credentials`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/observability/`
- **Build targets:** `hepta_observability_core`
- **Allowed module dependencies:** `hepta.protocol.contracts`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `telemetry.runtime.v1`
- **Consumes:** `hepta.metric-registry.v1`, `hepta.reason-code.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `bounded-process-local`
- **persistence:** `bounded-non-authoritative-export-buffer`
- **writer:** `single-owner`

- Telemetry buffers and aggregation state are non-authoritative and may be dropped according to policy.
- Audit records designated by other contracts remain owned by their authoritative module.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `per-thread-target`
- **shard key:** `thread-metric-target`
- **blocking io:** `exporter-thread-only`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `lossy-bounded`
- **overflow:** `typed-failure`

- Preserve timestamp/correlation semantics where possible without claiming total order.
- Use bounded queues; overload degrades telemetry according to policy, never domain correctness.

## Failure and Recovery

- **Risk-increase behavior:** `never-authorizes`
- **Safe-exit behavior:** `never-weaken`

- Resume export from bounded buffers where supported and report loss counters.
- Do not replay telemetry as domain commands.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Sampling, exporters, cardinality and retention are canonical operational configuration.
- Sensitive fields remain redacted regardless of exporter settings.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `telemetry-v1`

- Self-observe exporter health, queue depth, drops, cardinality, latency and storage failures.
- Registry IDs are the allowed stable dimensions.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Enforce redaction and least-privilege exporter credentials.
- Telemetry endpoints cannot expose broker credentials or raw secret-bearing configuration.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `metric-contracts`, `telemetry-cardinality`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Validate dashboards/alerts with fault injection and capacity tests.
- During incident, preserve critical audit signals while shedding noncritical telemetry.

### Known gaps and qualification boundaries

- Production retention and external backend qualification are deployment-specific evidence, not repository claims.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
