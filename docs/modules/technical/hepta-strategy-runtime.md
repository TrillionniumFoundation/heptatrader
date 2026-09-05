# Strategy Runtime Technical Guide

Status: generated current view
Applies to: `hepta.strategy.runtime` version `2.4.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-strategy-runtime.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-strategy-runtime.json`](../manifests/hepta-strategy-runtime.json)

## Current Implementation Evidence

- **Evidence state:** `bounded-implementation`
- **Resource guardrail profile:** `guardrail-1`
- **External qualification gates:** none

### Implemented repository scope

- StrategyProposal validation and canonical digest
- pinned artifact and configuration identity admission
- bounded checkpoint metadata lifecycle
- generation-fenced start, stop, quarantine and replacement control
- bounded Linux checkpoint payload persistence with exact digest selection
- verified opaque checkpoint restoration into admitted metadata
- bounded read-only artifact/config/model byte loading with pinned Ed25519 verification
- policy- and lifetime-bound verified metadata admission and start
- signed fixed-point bytecode execution in a kernel-restricted Linux x86-64 child
- fuel-limited proposal generation and explicit VM-state checkpoint recovery
- shared in-process invocation, address-space and instruction-budget reservations

### Excluded or not-current scope

- arbitrary native or Python strategy code execution
- general-purpose OS sandbox and inherited-address-space secrecy
- whole-service CPU, resident-memory and cross-process concurrency enforcement
- automatic checkpoint selection and supervised process restart policy
- cross-host checkpoint replication and authenticated global anti-rollback
- production signing-key provisioning, active-policy distribution and authenticated rollback protection
- package parsing, dynamic linking and executable semantic validation

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/strategy_runtime/`
- **Test evidence:** `tests/strategy_proposal_tests.cpp`, `tests/python/test_bounded_runtime_components.py`, `tests/strategy_checkpoint_store_tests.cpp`, `tests/python/test_strategy_checkpoint_store.py`, `tests/strategy_artifact_verifier_tests.cpp`, `tests/python/test_strategy_artifact_verifier.py`, `tests/strategy_bytecode_runtime_tests.cpp`, `tests/python/test_strategy_bytecode_runtime.py`, `tests/strategy_proposal_admission_tests.cpp`, `tests/python/test_strategy_proposal_admission.py`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Hosts bounded strategy logic that emits proposals under explicit identity, lifetime and resource limits.

This module is classified as `sandbox-runtime` in trust domain `untrusted-strategy` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Load registered strategy implementations and configuration.
- Produce versioned proposals with complete identity, snapshot and lifetime context.
- Enforce strategy isolation, quotas and quarantine outcomes.

### Non-responsibilities

- Does not allocate global capital or place venue orders.
- Does not possess broker credentials or Execution capability.
- Does not mark its own proposal accepted.

## Trust Domain and Authority

- **Declared authority:** StrategyProposal only
- **Trust domain:** `untrusted-strategy`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/strategy-platform`
- **Backup:** `@hepta/global-allocation`
- **Required reviewers:** `@hepta/security-runtime`
- **Forbidden dependencies:** `hepta.execution.runtime`, `hepta.venue.*`, `broker.credentials`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/strategy_runtime/`
- **Build targets:** `hepta_strategy_runtime`
- **Allowed module dependencies:** `hepta.protocol.contracts`, `hepta.numeric.core`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `hepta.strategy-proposal.v1`
- **Consumes:** `feature-snapshot.v1`, `hepta.module-lifecycle.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `module-isolated`
- **persistence:** `bounded-local-payload-store-plus-process-local-metadata`
- **writer:** `single-owner`

- Strategy-local state is isolated by strategy/agent identity and is never authoritative portfolio state.
- Proposal output is immutable and expires according to contract.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `shared-admission-plus-per-runner-child-and-separate-controller-store-mutexes`
- **shard key:** `strategy-agent-instance`
- **blocking io:** `runner-and-verified-stores-only-never-under-controller-lock`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `proposal-expiry`
- **overflow:** `typed-failure`

- Consume ordered feature/snapshot inputs according to the declared contract.
- Bound per-strategy work and output queues; overload causes throttling/quarantine, not system-wide lock contention.

## Failure and Recovery

- **Risk-increase behavior:** `cannot-authorize`
- **Safe-exit behavior:** `never-weaken`

- Restart isolated strategy state from approved checkpoint/configuration and reacquire session capability.
- Discard expired proposals after restart.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Strategy versions, parameters, resource budgets and enablement are canonical configuration.
- Unknown or unapproved strategy versions cannot load.

The manifest version is `2.4.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `strategy-v1`

- Report compute latency, proposal counts, expiry, quota violations and quarantine events.
- Keep proprietary model data out of shared telemetry.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Treat strategy code and output as untrusted relative to Decision and Execution.
- Sandbox resources and forbid dependencies on venue credentials or execution internals.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `proposal-contracts`, `strategy-isolation`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Promote through research validation, replay and simulator gates before enabling.
- Quarantine one strategy without weakening global risk or other tenants.

### Known gaps and qualification boundaries

- Production-grade sandboxing and model promotion evidence are deployment-specific beyond repository structure.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
