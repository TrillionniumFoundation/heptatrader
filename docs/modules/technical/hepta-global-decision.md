# Global Decision Runtime Technical Guide

Status: generated current view
Applies to: `hepta.global.decision` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-global-decision.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-global-decision.json`](../manifests/hepta-global-decision.json)

## Purpose and Scope

Performs deterministic global allocation across bounded strategy proposals and issues the sole decision capability consumed by Execution.

This module is classified as `solver-service` in trust domain `global-decision` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Validate proposal identity, lifetime, policy and snapshot context.
- Solve or approximate the registered allocation objective under portfolio/risk constraints.
- Produce deterministic AllocationPlan provenance and a construction-restricted decision receipt.

### Non-responsibilities

- Does not place venue orders or hold broker credentials.
- Does not trust strategy-provided capital allocations.
- Does not claim optimality when the solver status or bound does not prove it.

## Trust Domain and Authority

- **Declared authority:** proposal aggregation/global allocation
- **Trust domain:** `global-decision`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/global-allocation`
- **Backup:** `@hepta/risk`
- **Required reviewers:** `@hepta/research-validation`, `@hepta/execution-safety`
- **Forbidden dependencies:** `hepta.venue.*`, `hepta.execution.runtime`, `broker.credentials`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/proposal/`, `HeptaTrade/allocation/`
- **Build targets:** `hepta_proposal_aggregator`, `hepta_global_allocator`
- **Allowed module dependencies:** `hepta.management.control`, `hepta.numeric.core`, `hepta.portfolio.compiler`, `hepta.protocol.contracts`, `hepta.risk.policy`, `hepta.strategy.runtime`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `hepta.allocation-plan.v1`, `hepta.global-optimization.v1`, `hepta.solver-result.v1`, `proposal-set.v1`
- **Consumes:** `capital-policy.v1`, `hepta.authoritative-snapshot.v2`, `hepta.module-lifecycle.v1`, `hepta.numeric.fixed-v1`, `hepta.strategy-proposal.v1`, `proposal-set.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `decision-log`
- **persistence:** `none-recomputable-from-immutable-inputs`
- **writer:** `single-owner`

- Decision state is immutable per proposal set and policy/snapshot context.
- Plans retain accepted, rejected, objective, bound, gap and lifecycle provenance.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `capital-pool-sharded`
- **shard key:** `capital-pool-policy-revision`
- **blocking io:** `forbidden-on-solver-path`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `deadline-bounded`
- **overflow:** `typed-failure`

- Canonicalize proposal order before solving and make duplicate/conflict handling deterministic.
- Bound candidate count and solve time; timeout yields an explicit heuristic/failed status, never fabricated exactness.

## Failure and Recovery

- **Risk-increase behavior:** `no-plan`
- **Safe-exit behavior:** `never-weaken`

- Recompute from the same immutable inputs for replay; expired contexts require a new decision.
- Do not reuse receipts across policy, session, snapshot or lifetime changes.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Objective weights, cutoffs and constraints are policy-revisioned inputs.
- Configuration cannot expand execution authority and must be included in provenance.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `global-allocator-v1`

- Record solver status, objective, bound, gap, candidate counts, rejection reasons and latency.
- Avoid logging strategy secrets beyond registered identifiers and aggregate metrics.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Only Global Decision can construct the same-process decision receipt.
- A future cross-process path must add issuer authentication, audience, nonce, expiry and replay protection.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `proposal-contracts`, `optimizer-determinism`, `constraint-properties`, `shadow-parity`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Promote solver changes through replay and simulator parity before active use.
- Fallback is explicit deterministic heuristic or fail-closed denial according to policy.

### Known gaps and qualification boundaries

- External process separation still requires an authenticated decision envelope; IB PAPER qualification remains independent.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
