# Client Runtime Technical Guide

Status: generated current view
Applies to: `hepta.client.runtime` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-client-runtime.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-client-runtime.json`](../manifests/hepta-client-runtime.json)

## Current Implementation Evidence

- **Evidence state:** `bounded-implementation`
- **Resource guardrail profile:** `guardrail-1`
- **External qualification gates:** none

### Implemented repository scope

- native tool client
- CLI composition
- session-supervisor client transport

### Excluded or not-current scope

- stable public SDK compatibility guarantee
- remote multi-tenant client service

### Direct implementation evidence

- **Source evidence:** `HeptaTrade/client/`, `HeptaTrade/cli/`
- **Test evidence:** `tests/native_tool_client_tests.cpp`, `tests/unix_tool_server_tests.cpp`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Implements supported client entrypoints and protocol adapters for operators, native callers and agent-facing interfaces while preserving server-side authority checks.

This module is classified as `client-library` in trust domain `unprivileged-client` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Encode requests against versioned public contracts.
- Normalize client errors and reason codes without hiding server denials.
- Maintain compatibility boundaries for CLI, native and MCP-style entrypoints.

### Non-responsibilities

- Does not authorize actions locally or infer missing capabilities.
- Does not persist authoritative trading state.
- Does not directly access venue transports or broker credentials.

## Trust Domain and Authority

- **Declared authority:** CLI/native/MCP request encoding
- **Trust domain:** `unprivileged-client`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/sdk`
- **Backup:** `@hepta/gateway`
- **Required reviewers:** `@hepta/contracts`
- **Forbidden dependencies:** `hepta.execution.runtime`, `hepta.venue.*`, `broker.credentials`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/cli/hepta_sessionctl`, `HeptaTrade/cli/heptactl`, `HeptaTrade/client/`, `HeptaTrade/tool_host/unix_session_supervisor_client`, `HeptaTrade/tool_host/unix_tool_client`, `adapters/mcp/`, `plugins/heptatrader-agent-os/`
- **Build targets:** `hepta_native_tool_client`, `heptactl`, `hepta_sessionctl`
- **Allowed module dependencies:** `hepta.numeric.core`, `hepta.protocol.contracts`, `hepta.session.runtime`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `client.mcp.v1`, `client.native.v1`
- **Consumes:** `hepta.session-supervisor.v1`, `hepta.tool-catalog.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `none`
- **persistence:** `none-ephemeral-request-context`
- **writer:** `single-owner`

- Client state is ephemeral request/session context; authoritative state remains server-side.
- Retries must retain idempotency identity and must not synthesize success.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `caller-owned`
- **shard key:** `caller-owned`
- **blocking io:** `bounded-client-transport-only`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `caller-timeout`
- **overflow:** `typed-failure`

- Preserve request IDs and response ordering within the declared transport semantics.
- Apply bounded client-side buffering and expose timeout/backpressure failures explicitly.

## Failure and Recovery

- **Risk-increase behavior:** `not-authoritative`
- **Safe-exit behavior:** `never-weaken`

- Reconnect through the canonical session handshake and re-resolve capabilities.
- After ambiguous transport failure, query authoritative status instead of replaying mutations blindly.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Version negotiation, endpoints and timeouts come from canonical configuration.
- Unsupported protocol or schema versions fail closed with an explicit compatibility error.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `client-v1`

- Emit request latency, transport failures, retries and reason-code counts without sensitive payloads.
- Correlate logs with bounded request/session IDs.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Validate server identity where transport supports it and never cache long-lived secrets in logs or generated files.
- Treat all local convenience APIs as non-authoritative facades.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `native-client-contracts`, `mcp-contracts`, `tool-catalog-drift`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Canary new client versions against compatibility suites before broad distribution.
- Rollback is client package rollback; server authority remains unchanged.

### Known gaps and qualification boundaries

- Physical client variants remain subject to the capability registry; an entrypoint is not proof of environment qualification.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
