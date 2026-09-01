# AllocationPlan V1

Status: current normative
Applies to: Global Decision output and Execution shadow intake
Verification: plan integrity, provenance capability, lifetime, snapshot/context binding and Execution revalidation tests
Authority: global-decision output contract

`AllocationPlan` 是 immutable、bounded、可重放的目标计划，不是 broker command，也不授予 mutation。Plan 必须绑定 allocator epoch、capital pool、account book、policy revision、proposal-set digest、authoritative snapshot digest、proposal captured time、proposal validity ceiling、snapshot validity ceiling、SolverResult、fixed-point targets、created/valid-until、numeric policy 和 plan digest。

`valid_until_ms` 不能由调用者任意指定。`ProposalSetBuilder` 从同一 decision capture time 计算全部成员的有效交集：每个 proposal 的 `expires_at_ms`、以 proposal-set capture time 为起点的 `horizon_ms`，以及 authoritative snapshot 的 `snapshot_valid_until_ms`。`GlobalAllocator` 只能把该交集上界复制到 plan，并拒绝已经到期、尚未生效、时间回退或超过 snapshot lifetime 的输入。

当前 C++ shadow 路径使用 construction-restricted `GlobalDecisionReceipt`。默认 receipt 无效，只有 `GlobalAllocator` 可以从其实际输出创建有效 receipt；Execution 不能仅凭客户端可重算的无密钥 digest 接受任意 `AllocationPlan`。该 receipt 是同进程 typed capability，不是跨进程签名或 broker authorization；任何未来网络/PAPER intake 必须另行定义 authenticated authority envelope，并在此之前保持 absent/fail-closed。

Execution 接收 receipt 时还必须提供当前 `AllocationExecutionContext`，并逐项精确匹配 allocator epoch、capital pool、account book、policy revision、proposal-set digest、authoritative snapshot digest 与 snapshot valid-until。随后重新验证 plan/solver digest、exact/heuristic 状态与 bound/gap 一致性、半开时间窗口、target 排序/范围，再把 targets 交给现有 `PortfolioCompiler` 重算 strategy/global capital budget 和 authoritative generation delta。任一不一致均 typed reject；当前集成为 shadow revalidation，不直接发送 broker mutation。

机器 schema 为 `schemas/allocation-plan-v1.json`。Schema 对新增 identity/lifetime 字段采用 closed-world required validation；跨字段时间关系由 allocator/revalidator 的负向 CTest 执行。
