# Interactive Brokers Venue Adapter Technical Guide

Status: generated current view
Applies to: `hepta.venue.ib` version `1.0.0` (experimental)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-venue-ib.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-venue-ib.json`](../manifests/hepta-venue-ib.json)

## Current Implementation Evidence

- **Evidence state:** `external-qualification-required`
- **Resource guardrail profile:** `guardrail-1`
- **External qualification gates:** `G-IB-001`

### Implemented repository scope

- IB adapter state projection
- order lifecycle and risk wiring
- PAPER kill-switch controls

### Excluded or not-current scope

- qualified PAPER operation on an exact official SDK/host/account
- LIVE authorization

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/adapter_ib/`
- **Test evidence:** `tests/ib_order_lifecycle_tests.cpp`, `tests/ib_gateway_adapter_risk_tests.cpp`, `tests/ib_paper_kill_switch_tests.cpp`, `tests/python/test_ib_paper_qualification.py`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Adapts the pinned Interactive Brokers API to canonical Execution, market-data and reconciliation contracts for simulator-first and eventual PAPER qualification.

This module is classified as `venue-adapter` in trust domain `execution-authority` with lifecycle `experimental`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Map IB callbacks, identifiers and order lifecycle into canonical typed state.
- Keep SDK, account, session and credential handling inside the venue/Execution boundary.
- Support reconciliation and explicit uncertain outcomes across disconnects.

### Non-responsibilities

- Does not grant PAPER or LIVE qualification by compiling.
- Does not replace Decimal semantics with unverified binary-floating emulation.
- Does not submit orders outside Execution authority.

## Trust Domain and Authority

- **Declared authority:** IB PAPER transport/callback
- **Trust domain:** `execution-authority`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/venue-ib`
- **Backup:** `@hepta/execution-core`
- **Required reviewers:** `@hepta/execution-safety`, `@hepta/security-runtime`
- **Forbidden dependencies:** `hepta.gateway.runtime`, `hepta.global.decision`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/adapter_ib/`
- **Build targets:** `hepta_ibapi_client`, `hepta_ib_adapter_core`
- **Allowed module dependencies:** `hepta.protocol.contracts`, `hepta.risk.policy`, `hepta.observability.runtime`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `hepta.venue.v1`
- **Consumes:** `hepta.event-envelope.v1`, `hepta.risk-policy.v2`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `broker-observed`
- **persistence:** `process-local-adapter-state-reconciled-from-broker`
- **writer:** `single-owner`

- Adapter state includes connection/session, request/order ID mapping and callback-derived venue observations.
- Canonical OMS remains authoritative; adapter caches are reconciled after reconnect.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `single-projector`
- **shard key:** `broker-session-account`
- **blocking io:** `official-ib-transport-thread-only`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `lossless-authoritative`
- **overflow:** `typed-failure`

- Serialize callback-to-state transitions and preserve IB ordering/identifier semantics.
- Bound outbound/inbound queues and close mutation admission during disconnect or callback backlog.

## Failure and Recovery

- **Risk-increase behavior:** `fail-closed`
- **Safe-exit behavior:** `never-weaken`

- Reconnect under a fenced session, resynchronize IDs/state and reconcile open orders/executions before resuming.
- Uncertain mutations remain uncertain until venue evidence resolves them.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Pin exact IB API archive/tree, account/environment, gateway/TWS build, Decimal ABI and connection policy.
- PAPER enablement requires a verified qualification receipt for the exact artifact/configuration.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `ib-paper-v1`

- Track connection state, callback lag, request/order mapping, rejects, reconciliation lag and Decimal conversion failures.
- Redact account and credential material.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Credentials stay in the adapter/Execution trust boundary with least privilege.
- Qualification evidence binds code, binary, config, SDK, toolchain, account/session and scenario identities.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `ib-off-contracts`, `common-risk-parity`, `paper-qualification`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Keep disabled outside explicit simulator/PAPER profiles.
- PAPER rollout requires official SDK build, scenario matrix, soak, disconnect/reconnect and rollback evidence.

### Known gaps and qualification boundaries

- G-IB-001 remains external until real official-SDK PAPER evidence exists; repository hardening cannot fabricate it.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
