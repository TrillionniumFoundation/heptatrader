# Hepta Modular Runtime Global Development Roadmap V2

Status: generated current view
Applies to: all active development workstreams
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from milestone and gap registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

## Repository declarations and dependency diagnostics

Recorded states describe repository implementation declarations only. A recorded `closed` never means current-head checks, review, integration, release, external qualification or deployment succeeded.

| ID | Milestone | Recorded state | Depends on | Own non-closed gaps | Unresolved prerequisites (transitive) | Declaration diagnostics |
|---|---|---|---|---|---|---|
| `M0` | Canonical Truth Consolidation | `closed` | — | — | — | none (not verification) |
| `M1` | Documentation Control Plane | `in-progress` | M0 | — | — | none (not verification) |
| `M2` | Modular Runtime Foundation | `closed` | M1 | — | M1 | closed-with-unmet-prerequisites |
| `M3` | Typed Data and Concurrency Foundation | `closed` | M2 | — | M1 | closed-with-unmet-prerequisites |
| `M4` | Global Decision Shadow | `closed` | M3 | — | M1 | closed-with-unmet-prerequisites |
| `M5` | Active Multi-Agent Simulator | `closed` | M4 | — | M1 | closed-with-unmet-prerequisites |
| `M6` | IB PAPER Parity and Qualification | `in-progress` | M5 | G-IB-001 | M1 | none (not verification) |
| `M7` | Team-Scale Continuous Development | `in-progress` | M2 | G-TEAM-001 | M1 | none (not verification) |

Unresolved prerequisites include every ancestor with a non-closed declaration or its own non-closed registered gap. Diagnostics do not rewrite historical states or infer successful checks from zero open gaps.

## Exit contracts and independent integration gates

| ID | Repository exit contract | Required integration gate (not observed here) |
|---|---|---|
| `M0` | one active documentation graph; no historical docs or aliases; direct-main PR exact-head and merge-candidate CI | direct-main candidate must pass exact-head and exact merge-group controls and be independently accepted into main |
| `M1` | generated views deterministic; registries cross-validated; evidence schema integrated | documentation-control-plane must be required by the live no-bypass default-branch ruleset |
| `M2` | one target per module; no shared-migration source ownership; module-aware test impact | module graph and source ownership must pass on the final main merge-group revision |
| `M3` | single-source bindings; fixed numeric boundary; shard and queue contracts | fixed numeric, market authority and concurrency evidence must pass on the released exact revision |
| `M4` | StrategyProposal to AllocationPlan shadow E2E; deterministic solver record; Execution revalidation | shadow and allocation evidence must remain Simulator-scoped until a separately qualified environment consumes it |
| `M5` | module lifecycle; fault isolation; active capital allocation | Simulator release artifact must be reproducible and pass startup, replay, rollback and install-tree evidence |
| `M6` | exact-artifact qualification; fault scenarios; soak and rollback | protected real IB PAPER runner, official SDK, independent approval and broker-observed exact-artifact receipt |
| `M7` | team ownership; merge queue; module SLO and impact closure | live organization teams, team-only CODEOWNERS, active no-bypass ruleset, merge queue and governance receipt |

## Registered gaps are not the complete product backlog

Registered declarations: `planned`=0, `in-progress`=2, `blocked`=0, `closed`=31.

| Non-closed gap | Recorded state | Milestone | Scope |
|---|---|---|---|
| `G-IB-001` | `in-progress` | `M6` | IB PAPER lacks exact-artifact external qualification |
| `G-TEAM-001` | `in-progress` | `M7` | Platform CODEOWNERS are not yet team-distributed |

Module exclusions remain authoritative in [module implementation scope](../modules/module-registry-v2.json). They neither disappear when gap counts reach zero nor automatically become promised product scope. Remaining implementation work products are specified by the [current upgrade plan](DOCUMENTATION-UPGRADE-PLAN.md).

## Verification and authority observations

| Evidence dimension | Observation from this generator |
|---|---|
| `exact_head_checks` | `not-evaluated` |
| `independent_review` | `not-evaluated` |
| `merge_group_checks` | `not-evaluated` |
| `merged_main` | `not-evaluated` |
| `artifact_reproducibility` | `not-evaluated` |
| `external_qualification` | `not-evaluated` |
| `release_eligibility` | `not-evaluated` |
| `deployment_readiness` | `not-evaluated` |
| `paper_authority` | `not-evaluated` |
| `live_authority` | `not-evaluated` |

`grants_qualification=false`. This read-only declaration projection does not query GitHub, authenticate receipts, execute tests, change gap states or enable trading. Use independently bound live evidence for the dimensions above; see the [traceability model](TRACEABILITY-MODEL.md).

实时完成状态必须由 exact-revision evidence 派生；本视图不替代 CI 或外部 qualification。
