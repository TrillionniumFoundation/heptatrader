# SolverResult V1

Status: current target contract
Applies to: global allocator, AllocationPlan and decision evidence
Verification: schema, determinism, bound, timeout and numeric-failure tests
Authority: solver-result semantics

`SolverResult` 必须明确区分证明最优、容差最优、最佳已知、受限 fallback 和失败：

- `OPTIMAL`：求解器对声明模型给出可验证最优证明/状态；
- `OPTIMAL_WITHIN_TOLERANCE`：primal/dual residual 与 gap 在版本化容差内；
- `BEST_KNOWN`：非凸问题的最佳已知可行解，必须提供可用 bound 和 gap；
- `FEASIBLE_FALLBACK`：deadline/退化后通过全部 hard constraint 的保守方案；
- `NO_FEASIBLE_PLAN`、`STALE_INPUT`、`NUMERIC_FAILURE`：不得产生风险增加计划。

结果绑定 objective、solver binary/build、policy、input digest、seed、canonical ordering、tie-break、iterations、termination reason、lower/upper bound、gap 和 output digest。没有 bound 时不得填造零 gap。任何 fallback 均需经过独立 constraint validator 和 Execution risk。
