# Risk Policy Technical Guide

Status: generated current view
Applies to: `hepta.risk.policy` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-risk-policy.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-risk-policy.json`](../manifests/hepta-risk-policy.json)

## Current Implementation Evidence

- **Evidence state:** `bounded-implementation`
- **Resource guardrail profile:** `guardrail-1`
- **External qualification gates:** none

### Implemented repository scope

- deterministic pre-trade limits
- freshness and exposure rejection
- safe-exit preserving risk decisions

### Excluded or not-current scope

- product margin engines
- Greeks and scenario risk
- borrow and locate authority
- venue-specific regulatory policy

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/risk/`
- **Test evidence:** `tests/deterministic_risk_policy_tests.cpp`, `tests/risk_latency_fixture_tests.cpp`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Evaluates versioned, fail-closed risk policy and can only preserve or reduce permitted risk.

This module is classified as `pure-policy` in trust domain `portfolio-risk` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Validate policy revision, limits, overrides and input freshness.
- Return deterministic allow/reduce/deny outcomes with reason codes.
- Enforce monotonic risk reduction for emergency and fallback paths.

### Non-responsibilities

- Does not place orders or hold venue credentials.
- Does not expand authority beyond the incoming decision/session capability.
- Does not silently ignore missing limits or stale state.

## Trust Domain and Authority

- **Declared authority:** deterministic risk
- **Trust domain:** `portfolio-risk`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/risk`
- **Backup:** `@hepta/execution-safety`
- **Required reviewers:** `@hepta/state`
- **Forbidden dependencies:** `hepta.gateway.runtime`, `hepta.venue.*`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/risk/`
- **Build targets:** `hepta_risk_core`
- **Allowed module dependencies:** `hepta.protocol.contracts`, `hepta.numeric.core`, `hepta.observability.runtime`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `hepta.risk-policy.v2`
- **Consumes:** `hepta.authoritative-snapshot.v2`, `hepta.numeric.fixed-v1`, `hepta.reason-code.v1`, `portfolio.net-target.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `none`
- **persistence:** `none-immutable-policy-input`
- **writer:** `single-owner`

- Policy definitions are immutable per revision; evaluation is deterministic over explicit inputs.
- Overrides are separate audited objects with bounded scope and lifetime.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `pure-reentrant`
- **shard key:** `none-pure-reentrant`
- **blocking io:** `forbidden`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `caller-bounded`
- **overflow:** `typed-failure`

- Serialize conflicting policy revisions and reject stale evaluation context.
- Evaluation queues must be bounded; timeout denies or reduces according to registered policy.

## Failure and Recovery

- **Risk-increase behavior:** `fail-closed`
- **Safe-exit behavior:** `never-weaken`

- Reload the last verified policy revision and invalidate stale caches.
- On configuration ambiguity, apply the stricter valid policy or deny.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- All limits, hierarchies and override rules are canonical policy data.
- Breaking policy semantics require a new revision and replay qualification.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `risk-policy-v1`

- Report decisions, reductions, denials, override use, stale inputs and evaluation latency.
- Avoid leaking account-sensitive absolute values where aggregates suffice.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Separate policy authorship, approval and execution roles.
- Overrides require authenticated identity, reason, scope, expiry and audit trail.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `risk-properties`, `strict-reduction`, `numeric-negative`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Canary policy revisions in replay/simulator and compare counterfactual outcomes.
- Rollback selects a previously verified revision; emergency kill controls remain dominant.

### Known gaps and qualification boundaries

- Venue/account-specific PAPER evidence remains required for environment qualification.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
