# Global Optimization Contract V1

Status: current normative
Applies to: proposal aggregation and Global Decision solver
Verification: exact enumeration, bounded fallback, constraint, lifetime, provenance and digest tests
Authority: global-allocation semantics

输入必须是 expected module set 的完整、同 capital pool、同 account book、同 authoritative snapshot `ProposalSet`。集合必须在 allocator 的 `created_at_ms` 仍然有效，且其 `valid_until_ms` 不得超过 `snapshot_valid_until_ms`。调用方不能另行指定或延长 plan expiry；`AllocationPlan.valid_until_ms` 必须等于 ProposalSet 已计算的有效交集上界。

`GlobalAllocationPolicy` 必须携带 canonical `policy_revision`，并与 gross target、active instrument count、exact-combination cap 和每 instrument absolute limit 一起参与 plan digest。有限组合数不超过 policy cap 时，solver 枚举每个 module 的 reject/candidate 选择并给出可复验 `optimal`：upper bound = objective、primal bound = objective、gap = 0。超出 cap 时只返回 `feasible_not_proven`，同时记录 primal bound、独立松弛 upper bound 和 absolute gap；任何 heuristic 结果都不得标记 optimal。

约束使用 fixed-point 整数检查 instrument absolute limit、portfolio gross 和 active instrument count；tie-break 按规范化 module/candidate key，零收益默认 reject。SolverResult digest 绑定 status、objective/bounds/gap、explored count 与 exact flag。AllocationPlan digest 进一步绑定 allocator epoch、capital pool、account book、policy revision、proposal-set/snapshot identity、proposal/snapshot lifetime、targets、accepted/rejected lineage 和 numeric policy。机器 schema 分别为 `schemas/solver-result-v1.json` 与 `schemas/allocation-plan-v1.json`。

成功求解后，当前同进程实现由 `GlobalAllocator` 创建 construction-restricted `GlobalDecisionReceipt`。该 receipt 只证明对象由当前 allocator API 产生并阻止普通调用方把自行重算 digest 的任意 plan 直接交给 Execution；它不是数字签名、跨进程认证或 broker permit。任何未来跨进程/PAPER 接口必须另行提供 authenticated authority envelope，并在该协议完成前保持 absent/fail-closed。
