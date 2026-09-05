# Execution Runtime Technical Guide

Status: generated current view
Applies to: `hepta.execution.runtime` version `1.2.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-execution-runtime.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-execution-runtime.json`](../manifests/hepta-execution-runtime.json)

## Current Implementation Evidence

- **Evidence state:** `bounded-implementation`
- **Resource guardrail profile:** `guardrail-3`
- **External qualification gates:** `G-IB-001`

### Implemented repository scope

- single venue-mutation authority
- journal-before-send orchestration
- allocation-plan revalidation
- uncertain-command reconciliation

### Excluded or not-current scope

- externally qualified live broker operation
- multi-region active-active mutation authority

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/execution/`, `HeptaTrade/intent/`, `HeptaTrade/state/`
- **Test evidence:** `tests/execution_coordinator_tests.cpp`, `tests/allocation_plan_revalidator_tests.cpp`, `tests/oms_crash_replay_tests.cpp`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Owns the sole mutation authority for venue-facing order intent, order lifecycle, OMS journaling and reconciliation.

This module is classified as `stateful-service` in trust domain `execution-authority` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Validate decision receipts, risk policy, session capability and freshness before mutation.
- Serialize order lifecycle transitions and persist/replay the authoritative OMS journal.
- Handle uncertain outcomes through reconciliation rather than optimistic retry.

### Non-responsibilities

- Does not generate strategies or allocate global capital.
- Does not accept raw untrusted proposals as executable authority.
- Does not treat transport acknowledgement as final venue truth.

## Trust Domain and Authority

- **Declared authority:** only venue mutation/state/OMS/permit authority
- **Trust domain:** `execution-authority`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/execution-core`
- **Backup:** `@hepta/execution-safety`
- **Required reviewers:** `@hepta/oms-recovery`, `@hepta/risk`
- **Forbidden dependencies:** `hepta.gateway.runtime`, `hepta.management.control`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/cli/hepta_paper_terminal_latch_committer.cpp`, `HeptaTrade/execution/allocation_plan_revalidator`, `HeptaTrade/execution/execution_authoritative_`, `HeptaTrade/execution/execution_authority.h`, `HeptaTrade/execution/execution_coordinator`, `HeptaTrade/execution/execution_decision_`, `HeptaTrade/execution/execution_event_feed_client`, `HeptaTrade/execution/execution_event_feed_server`, `HeptaTrade/execution/execution_gateway_context_binding.h`, `HeptaTrade/execution/execution_place_order_dispatch.cpp`, `HeptaTrade/execution/execution_service_runtime_`, `HeptaTrade/execution/hepta_`, `HeptaTrade/execution/ib_`, `HeptaTrade/execution/paper_`, `HeptaTrade/execution/trading_contract.h`, `HeptaTrade/execution/unix_execution_service.cpp`, `HeptaTrade/execution/unix_execution_service.h`, `HeptaTrade/execution/unix_execution_service_client`, `HeptaTrade/execution/unix_execution_service_flatten`, `HeptaTrade/execution/unix_execution_service_internal.h`, `HeptaTrade/execution/unix_execution_service_server.h`, `HeptaTrade/intent/`, `HeptaTrade/oms_`, `HeptaTrade/state/`
- **Build targets:** `hepta_execution_client`, `hepta_execution_server`, `hepta_execution_core`, `hepta_executiond`, `hepta_ib_executiond`, `hepta_paper_terminal_latch_committer`, `hepta_oms_core`, `hepta_state_core`, `hepta_intent_core`, `hepta_allocation_revalidator`
- **Allowed module dependencies:** `hepta.agent.support`, `hepta.global.decision`, `hepta.numeric.core`, `hepta.observability.runtime`, `hepta.portfolio.compiler`, `hepta.protocol.contracts`, `hepta.risk.policy`, `hepta.strategy.runtime`, `hepta.venue.ib`, `hepta.venue.simulator`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `execution.client.v1`, `execution.permit.v1`, `hepta.authoritative-snapshot.v2`, `hepta.execution-authority.v1`, `hepta.oms-journal.v3`, `hepta.target-position-intent.v1`, `reconcile.decision.v1`
- **Consumes:** `decision-lease.v1`, `execution.permit.v1`, `hepta.allocation-plan.v1`, `hepta.authoritative-snapshot.v2`, `hepta.configuration-authority.v1`, `hepta.event-envelope.v1`, `hepta.execution-authority.v1`, `hepta.execution-wire.v1`, `hepta.numeric.fixed-v1`, `hepta.oms-journal.v3`, `hepta.reason-code.v1`, `hepta.risk-policy.v2`, `hepta.solver-result.v1`, `hepta.target-position-intent.v1`, `hepta.venue.v1`, `portfolio.net-target.v1`, `reconcile.decision.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `durable-authoritative`
- **persistence:** `durable-oms-journal-and-authoritative-state`
- **writer:** `single-owner`

- Order, intent, idempotency and reconciliation state are authoritative within the Execution boundary.
- Every mutation is bound to decision, policy, session and venue identity.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `execution-domain-sharded`
- **shard key:** `execution-domain-account-order`
- **blocking io:** `journal-and-venue-boundary-only`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `lossless-plus-emergency`
- **overflow:** `typed-failure`

- Apply deterministic per-order or per-account serialization and preserve venue sequence semantics.
- Admission queues are bounded; overflow and stale work fail closed before venue mutation.

## Failure and Recovery

- **Risk-increase behavior:** `fail-closed`
- **Safe-exit behavior:** `never-weaken`

- Replay the durable journal, reconcile with the venue and fence superseded sessions before resuming.
- Ambiguous outcomes enter explicit uncertain/reconciling states; never duplicate blindly.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Venue endpoints, account bindings, risk limits and enablement come from signed or canonical configuration.
- LIVE is unsupported unless the capability registry and qualification policy explicitly change.

The manifest version is `1.2.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `execution-authority-v1`

- Record state transitions, idempotency hits, rejection reasons, reconciliation lag and venue health.
- Protect account and credential material from logs and metrics.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Broker credentials remain inside the Execution/venue trust boundary.
- Require typed same-process capabilities or authenticated cross-process envelopes; ordinary serialized structs are insufficient.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `journal-durability`, `idempotency`, `snapshot-contracts`, `permit-lifecycle`, `reconciliation`, `simulator-e2e`, `crash-replay`, `shadow-parity`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Start only after configuration, journal and venue reconciliation gates pass.
- Kill switch, rollback and credential revocation take precedence over throughput.

### Known gaps and qualification boundaries

- IB PAPER remains externally blocked until real SDK, account, session and scenario evidence are verified.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
