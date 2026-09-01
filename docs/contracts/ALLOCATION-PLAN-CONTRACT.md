# AllocationPlan V1

Status: current normative
Applies to: Global Decision output and Execution shadow intake
Verification: plan integrity, expiry, snapshot binding and Execution revalidation CTests
Authority: global-decision output contract

`AllocationPlan` 是 immutable、bounded、可重放的目标计划，不是 broker command，也不授予 mutation。Plan 绑定 allocator epoch、proposal-set digest、snapshot digest、SolverResult、fixed-point targets、created/valid-until 和 plan digest。

Execution 依次验证 plan/solver digest、exact/heuristic 状态与 bound/gap 一致性、时间窗口、authoritative snapshot digest、target 排序/范围，再把 targets 交给现有 `PortfolioCompiler` 重算strategy/global capital budget 和 authoritative generation delta。失败保持 typed reject；当前集成为shadow revalidation，尚不直接发送 broker mutation。机器 schema 为 `schemas/allocation-plan-v1.json`。
