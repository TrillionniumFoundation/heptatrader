# Market Data Runtime Technical Guide

Status: generated current view
Applies to: `hepta.marketdata.runtime` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-marketdata-runtime.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-marketdata-runtime.json`](../manifests/hepta-marketdata-runtime.json)

## Purpose and Scope

Normalizes point-in-time market events, enforces producer ordering and issues authoritative same-process risk-ready snapshot receipts.

This module is classified as `data-plane` in trust domain `market-data` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Validate event identity, time envelope, quote invariants and fixed-point values.
- Enforce producer epoch/sequence, detect gaps and maintain monotonic per-key generation.
- Provide coherent snapshot reads and issue receipts only after integrity, continuity and freshness checks.

### Non-responsibilities

- Does not fabricate missing ticks or hide sequence gaps.
- Does not compute strategy decisions or place orders.
- Does not treat mutable diagnostic snapshots as authority.

## Trust Domain and Authority

- **Declared authority:** normalized point-in-time events
- **Trust domain:** `market-data`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/data-platform`
- **Backup:** `@hepta/venue-ib`
- **Required reviewers:** `@hepta/state`
- **Forbidden dependencies:** `hepta.execution.runtime`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/marketdata/`
- **Build targets:** `hepta_marketdata_core`
- **Allowed module dependencies:** `hepta.protocol.contracts`, `hepta.numeric.core`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `market-event.v1`
- **Consumes:** `hepta.event-envelope.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `single-writer-shard`
- **persistence:** `module-declared`
- **writer:** `single-owner`

- Per venue/instrument entries retain the latest normalized event, generation, sequence-gap state and digest.
- Raw snapshots are diagnostic/replay values; receipts are construction-restricted same-process capabilities.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `venue-instrument-sharded`
- **shard key:** `module-declared`
- **blocking io:** `declared-only`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `coalescing-gap`
- **overflow:** `typed-failure`

- Single-writer shard ordering is enforced by producer epoch and sequence.
- Capacity is bounded; vector reads lock target shards in canonical order for a coherent cut.

## Failure and Recovery

- **Risk-increase behavior:** `stale-closes-gate`
- **Safe-exit behavior:** `never-weaken`

- Rebuild state from a trusted feed/replay source and require a clean epoch start before risk-ready service.
- Gap state remains closed to risk consumers until an explicit new epoch resets continuity.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Feed identities, freshness windows, calendars and capacity are canonical configuration or upstream contract data.
- Unsupported time/identity formats fail closed.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `marketdata-v1`

- Report event rates, stale/invalid/gap outcomes, generation, shard contention, capacity and vector-read latency.
- Keep instrument cardinality bounded by admitted market keys.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Validate all feed input and separate normalization from execution authority.
- Same-process receipts must not be serialized as network proof.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `marketdata-ordering`, `sequence-gap`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Bring feeds online before dependent Feature/Decision readiness and verify clean sequence continuity.
- On feed anomaly, close the risk-ready gate and preserve diagnostics for replay.

### Known gaps and qualification boundaries

- Cross-process snapshot authority requires an authenticated envelope if the current in-process deployment boundary changes.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
