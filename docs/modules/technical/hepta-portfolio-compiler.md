# Portfolio Compiler Technical Guide

Status: generated current view
Applies to: `hepta.portfolio.compiler` version `1.0.0` (current)
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from `modules/manifests/hepta-portfolio-compiler.json`, module-documentation-profiles-v1.json and canonical registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

Manifest: [`modules/manifests/hepta-portfolio-compiler.json`](../manifests/hepta-portfolio-compiler.json)

## Purpose and Scope

Compiles approved allocation output into deterministic target-position intents while enforcing portfolio-level normalization and netting rules.

This module is classified as `pure-policy` in trust domain `portfolio-risk` with lifecycle `current`.

## Responsibilities and Non-Responsibilities

### Responsibilities

- Translate AllocationPlan targets into versioned target-position intents.
- Apply deterministic netting, lot/scale and portfolio identity rules.
- Retain decision and policy provenance through compilation.

### Non-responsibilities

- Does not choose the global allocation objective.
- Does not submit venue orders or bypass Execution revalidation.
- Does not infer missing instrument metadata permissively.

## Trust Domain and Authority

- **Declared authority:** deterministic netting/budget
- **Trust domain:** `portfolio-risk`
- **Ownership mode:** `exclusive`
- **DRI:** `@hepta/portfolio`
- **Backup:** `@hepta/risk`
- **Required reviewers:** `@hepta/architecture`
- **Forbidden dependencies:** `hepta.execution.runtime`, `hepta.venue.*`

Authority is limited to the statement above. A dependency, public type or transport message never grants additional runtime authority by itself.

## Physical Source and Build Boundaries

- **Source roots:** `HeptaTrade/portfolio/`
- **Build targets:** `hepta_portfolio_core`
- **Allowed module dependencies:** `hepta.protocol.contracts`, `hepta.numeric.core`

Physical ownership is verified against [`source-ownership-registry-v1.json`](../source-ownership-registry-v1.json) and the configured CMake File API graph. Cross-module compilation requires an exact, open-gap exception.

## Contracts and Public Interfaces

- **Provides:** `portfolio.net-target.v1`
- **Consumes:** `capital-policy.v1`, `hepta.allocation-plan.v1`, `hepta.authoritative-snapshot.v2`, `hepta.numeric.fixed-v1`, `hepta.strategy-proposal.v1`

Contract definitions, providers, consumers and compatibility state are resolved through the [canonical contract index](../../contracts/CONTRACT-INDEX.md). Inputs are validated before state admission; schema validity alone is not proof of issuer authority.

## State and Data Model

- **model:** `none`
- **persistence:** `none-recomputable-from-plan-and-metadata`
- **writer:** `single-owner`

- Compilation is a deterministic transformation; outputs are immutable intents bound to the input plan.
- Any caches are derived and invalidated by plan, policy or instrument-metadata revision.

## Concurrency, Ordering, and Backpressure

### Concurrency contract

- **model:** `pure-reentrant`
- **shard key:** `none-pure-reentrant`
- **blocking io:** `forbidden`
- **cross module lock:** `forbidden`

### Backpressure contract

- **class:** `caller-bounded`
- **overflow:** `typed-failure`

- Canonicalize instruments and accounts before netting.
- Bound target count and reject duplicates, conflicts or capacity overflow explicitly.

## Failure and Recovery

- **Risk-increase behavior:** `fail-closed`
- **Safe-exit behavior:** `never-weaken`

- Recompile from the same plan and metadata for replay.
- Expired or superseded plans require a new authoritative input, not repair in place.

Failures never authorize a weaker validation path. Recovery begins from authoritative state, preserves fencing and emits a typed reason code.

## Configuration and Compatibility

- Lot size, instrument mapping and portfolio rules are versioned metadata/configuration.
- Unsupported venue/instrument combinations fail closed.

The manifest version is `1.0.0`. Contract or behavior changes that alter authority, state, failure or compatibility semantics require a governed version and registry update.

## Observability and Resource Budgets

- **Resource budget:** `portfolio-compiler-v1`

- Record target counts, netting reductions, rejected mappings, numeric failures and compilation latency.
- Correlate every intent to plan and policy identity.

Telemetry is diagnostic unless another contract explicitly designates it as authoritative evidence. Queues, labels and retained payloads remain bounded.

## Security

- Accept only the registered decision/plan contract from the allowed dependency boundary.
- Compilation cannot grant additional quantity, account or venue authority.

The module follows least privilege and must not expose secrets, credentials or capabilities outside its declared trust boundary.

## Verification and Testing

- **Required verification IDs:** `portfolio-properties`, `budget-overflow`, `canonical-ordering`, `module-documentation-coverage`

Each ID resolves through the [verification test matrix](../../verification/test-matrix-v2.json). Module changes require positive, negative and relevant fault-path evidence on the same exact revision.

## Operations, Rollout, and Known Gaps

### Operations and rollout

- Promote compiler changes through golden portfolio vectors and simulator execution parity.
- Rollback restores prior mapping/rule versions and recompiles pending non-mutated work.

### Known gaps and qualification boundaries

- Venue-specific execution qualification remains outside the compiler boundary.

Open and closed program gaps are authoritative only in the [gap registry](../../program/gap-registry-v2.json); this guide does not fabricate external qualification, human approval or production authority.
