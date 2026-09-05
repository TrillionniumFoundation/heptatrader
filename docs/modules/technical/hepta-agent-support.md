# Agent Support Runtime Technical Guide

Status: generated current view
Applies to: `hepta.agent.support` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-agent-support.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-agent-support.json`](../manifests/hepta-agent-support.json)

## Current Implementation Evidence

- **Evidence state:** `bounded-implementation`
- **Resource guardrail profile:** `guardrail-1`
- **External qualification gates:** none

### Implemented repository scope

- decision lease client support
- typed execution event fan-out
- authority-preserving agent helpers

### Excluded or not-current scope

- general autonomous-agent scheduler
- credential ownership
- direct venue mutation

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/agent/`, `HeptaTrade/events/`
- **Test evidence:** `tests/decision_lease_manager_tests.cpp`, `tests/execution_event_hub_tests.cpp`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Provides bounded support services used by agent processes, including event distribution, lease-aware coordination helpers and non-authoritative runtime utilities.

This module is classified as `support-library` in trust domain `trusted-local` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Expose support primitives through typed interfaces without granting execution or broker authority.
- Maintain bounded queues and lifecycle-aware support state for agent consumers.
- Translate support-layer failures into explicit, fail-closed outcomes.

### Non-responsibilities

- Does not choose trades, allocate capital or mutate venue state.
- Does not own broker credentials or authoritative portfolio state.
- Does not weaken session fencing or bypass module contracts.

## Trust Domain and Authority

- **Declared authority:** decision lease client and event hub
- **Trust domain:** `trusted-local`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/agent-runtime`
- **Backup:** `@hepta/execution-core`
- **Required reviewers:** `@hepta/security-runtime`
- **Forbidden dependencies:** `hepta.venue.*`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/agent/`, `HeptaTrade/events/`
- **Build targets:** `hepta_agent_execution_support`
- **Allowed module dependencies:** `hepta.protocol.contracts`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `decision-lease.v1`
- **Consumes:** `hepta.event-envelope.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `bounded-local`
- **persistence:** `process-local-reconstructible-cache`
- **writer:** `single-owner`

- Support state is subordinate to the owning session and must be disposable or reconstructible.
- Any cached event or lease view is advisory unless its source contract explicitly grants authority.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `owner-sharded`
- **shard key:** `agent-session-owner`
- **blocking io:** `forbidden-on-event-dispatch`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `bounded-event-buffer`
- **overflow:** `typed-failure`

- Preserve event identity and ordering metadata when relaying messages.
- Bound queue growth; overflow must surface through the manifest-declared typed failure rather than silent loss.

## Failure and Recovery

- **Risk-increase behavior:** `lease-expiry-reject`
- **Safe-exit behavior:** `never-weaken`

- Restart from authoritative upstream state and reacquire leases before serving dependent agents.
- Reject stale epochs and invalidate cached support state after fencing changes.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Accept only configuration resolved by the canonical configuration authority.
- Configuration changes must not expand authority without a manifest and contract revision.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `agent-support-v1`

- Report queue depth, dropped/coalesced events, lease state and restart counts.
- Keep dimensions bounded by registered agent/session identifiers.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Treat agent inputs as untrusted and validate size, identity and capability before dispatch.
- Never expose credentials or Execution-only endpoints through support helpers.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `lease-fencing`, `event-ordering`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Roll out behind bounded agent populations and verify lease/fencing metrics before expansion.
- On anomaly, stop serving new work and preserve diagnostics without retaining authority.

### Known gaps and qualification boundaries

- No module-local release blocker is declared; product qualification and team-governance gates still apply.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
