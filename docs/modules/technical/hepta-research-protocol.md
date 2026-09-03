# Research Protocol Runtime Technical Guide

Status: generated current view
Applies to: `hepta.research.protocol` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-research-protocol.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-research-protocol.json`](../manifests/hepta-research-protocol.json)

## Current Implementation Evidence

- **Evidence state:** `contract-only`
- **Resource guardrail profile:** `guardrail-1`
- **External qualification gates:** none

### Implemented repository scope

- purged walk-forward protocol
- embargo/final-OOS controls
- research manifest and deterministic verification fixtures

### Excluded or not-current scope

- production point-in-time data lake
- broad licensed dataset catalog
- experiment service
- production feature store

### Direct implementation evidence

- **Source evidence:** `research/`, `scripts/check_research_registries.py`
- **Test evidence:** `tests/python/test_research_protocol.py`, `tests/python/test_research_registries.py`

This section is the current repository-scope capability ceiling. The target contract below may describe future or deployment-dependent behavior, but it cannot raise the evidence state, erase exclusions, close an external gate, or imply PAPER/LIVE/deployment qualification.

## Purpose and Scope

Runs deterministic research and replay protocols under bounded resources without granting production trading authority.

This module is classified as `offline-runtime` in trust domain `capability-free-research` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Validate datasets, protocol manifests and point-in-time constraints.
- Execute reproducible research/replay commands with captured provenance.
- Produce evidence suitable for review without promoting models automatically.

### Non-responsibilities

- Does not access broker credentials or submit orders.
- Does not label a result production-qualified without promotion gates.
- Does not read future data into point-in-time experiments.

## Trust Domain and Authority

- **Declared authority:** deterministic replay only
- **Trust domain:** `capability-free-research`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/research-validation`
- **Backup:** `@hepta/data-platform`
- **Required reviewers:** `@hepta/security-runtime`
- **Forbidden dependencies:** `hepta.execution.runtime`, `broker.credentials`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `research/`
- **Build targets:** none
- **Allowed module dependencies:** none

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `hepta.research-run.v1`
- **Consumes:** none

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `append-only-run`
- **persistence:** `append-only-run-artifacts`
- **writer:** `single-owner`

- Runs are immutable evidence bundles keyed by protocol, dataset and code identity.
- Temporary workspace state is disposable; registries remain canonical.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `run-isolated`
- **shard key:** `research-run`
- **blocking io:** `offline-runner-only`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `offline-bounded`
- **overflow:** `typed-failure`

- Protocol steps follow declared deterministic order and bounded parallelism.
- Resource overflow terminates or rejects the run with explicit evidence.

## Failure and Recovery

- **Risk-increase behavior:** `never-authorizes`
- **Safe-exit behavior:** `never-weaken`

- Resume only where the protocol defines checkpoint semantics; otherwise rerun from immutable inputs.
- Corrupt or incomplete evidence cannot be promoted.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Seeds, datasets, feature versions and resource limits are explicit manifest inputs.
- Environment differences are recorded rather than normalized away silently.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `research-run-v1`

- Record run identity, durations, resource use, failures and artifact digests.
- Protect proprietary datasets and secrets from logs/artifacts.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Sandbox untrusted research inputs and prohibit production credentials.
- Promotion requires separate reviewed contracts and qualification.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `research-self-test`, `point-in-time`, `cost-model`, `digest-parity`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Use reproducible runners and immutable evidence storage.
- Delete or quarantine incomplete runs; never merge them into authoritative qualification evidence.

### Known gaps and qualification boundaries

- External dataset licenses and production model approval are outside repository-only closure.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
