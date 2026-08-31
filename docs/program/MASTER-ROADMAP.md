# Hepta Modular Runtime Global Development Roadmap V2

Status: generated current view
Applies to: all active development workstreams
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from milestone and gap registries

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

| ID | Milestone | State | Depends on | Open/blocked gaps | Exit contract |
|---|---|---|---|---:|---|
| `M0` | Canonical Truth Consolidation | **closed** | — | 0 | one active documentation graph; no historical docs or aliases; direct-main PR exact-head and merge-candidate CI |
| `M1` | Documentation Control Plane | **closed** | M0 | 0 | generated views deterministic; registries cross-validated; evidence schema integrated |
| `M2` | Modular Runtime Foundation | **in-progress** | M1 | 2 | one target per module; no shared-migration source ownership; module-aware test impact |
| `M3` | Typed Data and Concurrency Foundation | **planned** | M2 | 4 | single-source bindings; fixed numeric boundary; shard and queue contracts |
| `M4` | Global Decision Shadow | **planned** | M3 | 3 | StrategyProposal to AllocationPlan shadow E2E; deterministic solver record; Execution revalidation |
| `M5` | Active Multi-Agent Simulator | **planned** | M4 | 1 | module lifecycle; fault isolation; active capital allocation |
| `M6` | IB PAPER Parity and Qualification | **planned** | M5 | 1 | exact-artifact qualification; fault scenarios; soak and rollback |
| `M7` | Team-Scale Continuous Development | **planned** | M2 | 2 | team ownership; merge queue; module SLO and impact closure |

实时完成状态必须由 exact-revision evidence 派生；本视图不替代 CI 或外部 qualification。
