# Gateway Runtime Technical Guide

Status: generated current view
Applies to: `hepta.gateway.runtime` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-gateway-runtime.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-gateway-runtime.json`](../manifests/hepta-gateway-runtime.json)

## Purpose and Scope

Provides the bounded server-side ingress, capability dispatch and protocol routing boundary for client and agent requests.

This module is classified as `service` in trust domain `agent-gateway` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Authenticate or validate session identity before capability dispatch.
- Route requests only to registered handlers and preserve typed reason codes.
- Enforce request size, concurrency, timeout and queue limits.

### Non-responsibilities

- Does not decide strategy or risk policy.
- Does not possess venue mutation authority except by forwarding to Execution under validated capability.
- Does not convert malformed requests into permissive defaults.

## Trust Domain and Authority

- **Declared authority:** identity/session/capability/tool dispatch
- **Trust domain:** `agent-gateway`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/gateway`
- **Backup:** `@hepta/security-runtime`
- **Required reviewers:** `@hepta/architecture`
- **Forbidden dependencies:** `hepta.venue.*`, `broker.credentials`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/tool_host/agent_`, `HeptaTrade/tool_host/execution_`, `HeptaTrade/tool_host/hepta_tool_gatewayd.cpp`, `HeptaTrade/tool_host/session_supervisor_audit_`, `HeptaTrade/tool_host/tool_`, `HeptaTrade/tool_host/trading_`, `HeptaTrade/tool_host/unix_session_supervisor_server`, `HeptaTrade/tool_host/unix_socket_path_identity.h`, `HeptaTrade/tool_host/unix_tool_server`, `HeptaTrade/tools/`
- **Build targets:** `hepta_agent_os_core`, `hepta_tool_gatewayd`, `hepta_trading_tool_core`, `hepta_execution_event_relay_core`
- **Allowed module dependencies:** `hepta.agent.support`, `hepta.client.runtime`, `hepta.execution.runtime`, `hepta.numeric.core`, `hepta.observability.runtime`, `hepta.protocol.contracts`, `hepta.session.runtime`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `gateway.session-boundary`, `gateway.tool-dispatch`, `hepta.identity-capability.v1`, `hepta.tool-catalog.v1`
- **Consumes:** `execution.client.v1`, `hepta.configuration-authority.v1`, `hepta.execution-authority.v1`, `hepta.execution-wire.v1`, `hepta.identity-capability.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `durable-session-audit`
- **persistence:** `module-declared`
- **writer:** `single-owner`

- Gateway state contains bounded connections, sessions and in-flight requests; domain authority remains in downstream modules.
- Capability caches are invalidated on session epoch or policy revision changes.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `owner-sharded-control`
- **shard key:** `module-declared`
- **blocking io:** `declared-only`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `bounded-owner-queue`
- **overflow:** `typed-failure`

- Preserve per-request identity and declared streaming order.
- Use bounded admission and fair scheduling; overload produces explicit rejection rather than hidden queue growth.

## Failure and Recovery

- **Risk-increase behavior:** `fail-closed`
- **Safe-exit behavior:** `never-weaken`

- Close or fence stale connections, re-establish session state and retry only idempotent operations.
- After downstream ambiguity, return an uncertain result and require authoritative status lookup.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Listeners, protocol versions, request budgets and downstream endpoints are canonical configuration.
- Unknown routes and disabled capabilities fail closed.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `gateway-control-v1`

- Expose connection counts, admission latency, queue depth, handler failures and reason codes.
- Redact credentials, tokens and sensitive payload fields.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Treat all network/client input as untrusted.
- Separate authentication, authorization and downstream domain validation; passing one does not imply the others.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `gateway-boundary`, `session-boundary`, `backpressure-contracts`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Deploy with readiness gates for session and downstream dependencies.
- Drain connections before rollback and preserve in-flight mutation correlation IDs.

### Known gaps and qualification boundaries

- Future physical process splits require authenticated envelopes for authorities currently represented by same-process types.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
