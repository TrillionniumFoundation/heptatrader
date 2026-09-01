# Global Optimization Contract V1

Status: current normative
Applies to: proposal aggregation and Global Decision solver
Verification: exact enumeration, bounded fallback, constraint and digest CTests
Authority: global-allocation semantics

输入必须是 expected module set 的完整、同 book、同 snapshot `ProposalSet`。有限组合数不超过 policy cap 时，solver 枚举每个 module 的 reject/candidate 选择并给出可复验 `optimal`：upper bound = objective、gap = 0。超出 cap 时只返回`feasible_not_proven`，同时记录 primal lower bound、独立松弛 upper bound 和 absolute gap；任何 heuristic 结果都不得标记 optimal。

约束使用 fixed-point 整数检查 instrument absolute limit、portfolio gross 和 active instrument count；tie-break 按规范化 module/candidate key，零收益默认 reject。SolverResult 与 plan 都绑定 SHA-256。机器 schema 为 `schemas/solver-result-v1.json`。
