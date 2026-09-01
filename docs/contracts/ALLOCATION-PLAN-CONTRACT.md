# AllocationPlan V1

Status: current normative
Applies to: Global Decision output and Execution shadow intake
Verification: plan integrity, expiry, snapshot binding and Execution revalidation CTests
Authority: global-decision output contract

`AllocationPlan` 是 immutable、bounded、可重放的目标计划，不是 broker command，也不授予 mutation。Plan 绑定 allocator epoch、proposal-set digest、snapshot digest、SolverResult、fixed-point targets、created/valid-until 和 plan digest。

Execution 依次验证 plan/solver digest、exact/heuristic 状态与 bound/gap 一致性、时间窗口、authoritative snapshot digest、target 排序/范围，再把 targets 交给现有 `PortfolioCompiler` 重算strategy/global capital budget 和 authoritative generation delta。失败保持 typed reject；当前集成为shadow revalidation，尚不直接发送 broker mutation。机器 schema 为 `schemas/allocation-plan-v1.json`。

## Sealed provenance and Execution context binding

An `AllocationPlan` binds allocator epoch, capital pool, account book, policy revision, ProposalSet digest, authoritative snapshot digest, ProposalSet capture/expiry and snapshot expiry. These fields, solver evidence and ordered targets are covered by the plan digest. Plan validity is derived from the ProposalSet/snapshot intersection and cannot be extended by a caller.

Execution accepts only a `GlobalDecisionReceipt` issued by Global Decision plus an independently supplied authoritative execution context. Default, forged or client-reconstructed receipts are rejected. Execution rechecks receipt integrity, solver bounds and gap, allocator epoch, pool, book, policy revision, ProposalSet identity, snapshot identity and lifetime, then recompiles targets against authoritative portfolio state and current execution budgets. Any mismatch yields no venue mutation.
