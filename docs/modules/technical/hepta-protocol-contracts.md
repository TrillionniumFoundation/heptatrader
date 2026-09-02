# Protocol Contracts Technical Guide

Status: generated current view
Applies to: `hepta.protocol.contracts` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-protocol-contracts.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-protocol-contracts.json`](../manifests/hepta-protocol-contracts.json)

## Purpose and Scope

Owns versioned schemas, canonical serialization rules and compatibility boundaries for inter-module messages.

This module is classified as `contract-library` in trust domain `shared-trusted` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Define closed-world schemas and required identity/lifetime/provenance fields.
- Generate or validate bindings and schema catalogs deterministically.
- Classify compatible versus breaking contract changes.

### Non-responsibilities

- Does not implement domain authority merely by defining a message.
- Does not allow unknown fields or silent defaulting on authoritative paths.
- Does not substitute schema validity for authenticated authority.

## Trust Domain and Authority

- **Declared authority:** wire/codec
- **Trust domain:** `shared-trusted`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/contracts`
- **Backup:** `@hepta/architecture`
- **Required reviewers:** `@hepta/execution-safety`
- **Forbidden dependencies:** `hepta.venue.*`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/execution/execution_event_feed.cpp`, `HeptaTrade/execution/execution_event_feed.h`, `HeptaTrade/execution/execution_event_feed_contract.h`, `HeptaTrade/execution/execution_event_feed_transport`, `HeptaTrade/execution/execution_service_protocol`, `HeptaTrade/execution/unix_execution_service_transport.cpp`, `HeptaTrade/tool_host/typed_`, `schemas/`
- **Build targets:** `hepta_execution_contract`, `hepta_execution_transport`, `hepta_tool_protocol`
- **Allowed module dependencies:** `hepta.numeric.core`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `hepta.event-envelope.v1`, `hepta.execution-wire.v1`
- **Consumes:** none

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `none`
- **persistence:** `none`
- **writer:** `single-owner`

- Contracts are immutable per version; new incompatible semantics require a new version.
- Schema catalogs and generated bindings are deterministic derived artifacts.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `pure-reentrant`
- **shard key:** `none`
- **blocking io:** `forbidden`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `caller-bounded`
- **overflow:** `typed-failure`

- Ordering fields are explicit contract data; the module itself has no runtime queue.
- Consumers must validate before admitting data to their own state machines.

## Failure and Recovery

- **Risk-increase behavior:** `not-applicable`
- **Safe-exit behavior:** `never-weaken`

- Fix source schema/contract documents, regenerate artifacts and rerun compatibility tests.
- Never patch a released meaning in place without the governed compatibility process.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Schema selection follows explicit negotiated or configured version.
- Unsupported versions fail closed.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `contract-library-v1`

- Expose validation/compatibility failure counts at consuming boundaries.
- Diagnostics identify schema path without logging sensitive payloads.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Use closed-world validation, bounded sizes and canonical encodings.
- Authentication, issuer and audience semantics must be specified separately from structural schema.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `schema-catalog`, `protocol-contracts`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Contract changes require provider/consumer updates, negative tests and migration notes in one revision.
- Rollback restores the prior compatible contract set; mixed versions require explicit support.

### Known gaps and qualification boundaries

- Cross-process authority envelopes must add cryptographic/authentication semantics beyond ordinary schemas.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
