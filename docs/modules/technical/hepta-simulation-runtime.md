# Simulation Runtime Technical Guide

Status: generated current view
Applies to: `hepta.simulation.runtime` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-simulation-runtime.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-simulation-runtime.json`](../manifests/hepta-simulation-runtime.json)

## Purpose and Scope

Provides deterministic clocks, scheduling, scenarios and fault controls for simulator qualification and replay.

This module is classified as `simulation-orchestrator` in trust domain `simulation-control` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Advance virtual time and event schedules deterministically.
- Inject registered faults and preserve scenario identity/provenance.
- Coordinate simulator components without accessing real venue authority.

### Non-responsibilities

- Does not claim real-market latency or fill realism without evidence.
- Does not connect to PAPER/LIVE credentials.
- Does not allow wall-clock races to alter deterministic results.

## Trust Domain and Authority

- **Declared authority:** simulation-only multi-agent orchestration without broker mutation authority
- **Trust domain:** `simulation-control`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/simulator`
- **Backup:** `@hepta/global-allocation`
- **Required reviewers:** `@hepta/architecture`, `@hepta/execution-safety`
- **Forbidden dependencies:** `broker.credentials`, `hepta.gateway.runtime`, `hepta.venue.*`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/simulator/multi_agent_allocation`
- **Build targets:** `hepta_multi_agent_simulator`
- **Allowed module dependencies:** `hepta.execution.runtime`, `hepta.global.decision`, `hepta.management.control`, `hepta.numeric.core`, `hepta.portfolio.compiler`, `hepta.strategy.runtime`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** none
- **Consumes:** `hepta.allocation-plan.v1`, `hepta.authoritative-snapshot.v2`, `hepta.global-optimization.v1`, `hepta.module-lifecycle.v1`, `hepta.numeric.fixed-v1`, `hepta.solver-result.v1`, `hepta.strategy-proposal.v1`, `portfolio.net-target.v1`, `proposal-set.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `ephemeral-cycle`
- **persistence:** `none`
- **writer:** `single-owner`

- Scenario state is isolated per run and keyed by seed, clock and scenario version.
- Artifacts are replayable evidence, not production state.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `capital-pool-cycle`
- **shard key:** `capital-pool`
- **blocking io:** `forbidden`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `bounded-proposal-set`
- **overflow:** `typed-failure`

- Use a canonical event queue and deterministic tie-breaking.
- Bound scenario event count and resource usage; overflow fails the run explicitly.

## Failure and Recovery

- **Risk-increase behavior:** `no-plan`
- **Safe-exit behavior:** `never-weaken`

- Restart from an explicit checkpoint or rerun from immutable scenario inputs.
- Partial runs are marked incomplete and cannot satisfy qualification.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Clock, seed, latency, fault and scenario parameters are explicit versioned inputs.
- Defaults are recorded in evidence, not hidden process globals.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `multi-agent-simulator-v1`

- Record virtual time, event counts, injected faults, queue depth and determinism digests.
- Separate simulator metrics from real environment telemetry.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Prohibit real broker credentials and network mutation in simulator profiles.
- Fault controls are test-only capabilities unavailable to production entrypoints.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `proposal-completeness`, `optimizer-determinism`, `constraint-properties`, `shadow-parity`, `lifecycle-faults`, `rollout-rollback`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Run cross-compiler/replay determinism suites before accepting changes.
- Rollback restores prior scenario/model versions and compares golden digests.

### Known gaps and qualification boundaries

- Simulator success cannot close IB PAPER or LIVE qualification gaps.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
