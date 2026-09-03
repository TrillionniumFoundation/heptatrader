# Simulator Venue Adapter Technical Guide

Status: generated current view
Applies to: `hepta.venue.simulator` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-venue-simulator.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-venue-simulator.json`](../manifests/hepta-venue-simulator.json)

## Current Implementation Evidence

- **Evidence state:** `bounded-implementation`
- **Resource guardrail profile:** `guardrail-1`
- **External qualification gates:** none

### Implemented repository scope

- deterministic correctness simulator
- order/cancel/recovery fixture
- agent-to-execution end-to-end simulation

### Excluded or not-current scope

- queue-position model
- stochastic partial fills
- market impact and auction realism

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/simulator/`
- **Test evidence:** `tests/agent_simulator_e2e_tests.cpp`, `tests/multi_agent_allocation_tests.cpp`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Implements the deterministic simulated venue used for execution, reconciliation and failure-path verification.

This module is classified as `venue-adapter` in trust domain `execution-authority` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Model accepted/rejected/cancelled/filled order transitions deterministically.
- Apply registered latency, fill and fault rules under virtual time.
- Expose canonical venue observations to Execution and tests.

### Non-responsibilities

- Does not claim real venue market quality or latency.
- Does not connect to external broker credentials.
- Does not silently use wall-clock randomness.

## Trust Domain and Authority

- **Declared authority:** deterministic simulated venue
- **Trust domain:** `execution-authority`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/simulator`
- **Backup:** `@hepta/execution-core`
- **Required reviewers:** `@hepta/research-validation`
- **Forbidden dependencies:** `hepta.gateway.runtime`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/simulator/deterministic_execution_venue`
- **Build targets:** `hepta_simulator_venue`
- **Allowed module dependencies:** `hepta.protocol.contracts`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `hepta.venue.v1`
- **Consumes:** `hepta.event-envelope.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `deterministic-fixture`
- **persistence:** `scenario-local-replayable-state`
- **writer:** `single-owner`

- Order book, order lifecycle and fault state are isolated per scenario/run.
- All mutations are replayable from scenario inputs and canonical seeds.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `single-writer`
- **shard key:** `scenario-order-book`
- **blocking io:** `forbidden`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `deterministic-bounded`
- **overflow:** `typed-failure`

- Use deterministic event ordering and tie-breaking for fills, cancels and disconnect races.
- Bound simulated queues and fail scenarios explicitly on resource exhaustion.

## Failure and Recovery

- **Risk-increase behavior:** `deterministic-reject`
- **Safe-exit behavior:** `never-weaken`

- Restore from declared simulator checkpoint or replay from run start.
- Reconnect/fault recovery follows scenario rules and produces evidence.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Matching, latency, fill, reject and fault models are versioned scenario inputs.
- Unknown model versions fail closed.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `simulator-v1`

- Record simulated event order, fill decisions, injected faults, queue depth and determinism digests.
- Clearly label all telemetry as simulator environment.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Simulator profiles prohibit real credentials and real venue endpoints.
- Test-only fault controls are not exposed through production capability paths.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `simulator-e2e`, `deterministic-replay`, `fault-matrix`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Run golden lifecycle and race scenarios on every relevant change.
- Rollback selects prior model/scenario versions and compares deterministic outputs.

### Known gaps and qualification boundaries

- Simulator evidence is necessary but cannot substitute for IB PAPER qualification.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
