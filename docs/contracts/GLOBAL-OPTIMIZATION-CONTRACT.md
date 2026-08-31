# 全局优化契约 V1

Status: current target contract
Applies to: ProposalAggregator, GlobalAllocator, PortfolioCompiler and portfolio-level risk
Verification: determinism, feasibility, optimality, bound, timeout, shadow and replay tests
Authority: global optimizer objective and constraint semantics

GlobalAllocator 在一个完整 proposal set、authoritative snapshot vector、capital policy 和 resource policy 上求解 AllocationPlan。逻辑上全局，物理上按 capital pool/account/risk book 分片；上层资本预算以较慢周期协调，订单热路径不等待全局重算。

## Objective

基准目标为：

\[
\max_x\;\sum_m w_m U_m(x_m)
-\lambda_R R(\sum_m x_m)
-C(x-x_{current})
-\lambda_T T(x)
-\lambda_{tail}\operatorname{CVaR}(x)
-\lambda_Q Q(\text{compute, latency, data})
\]

所有权重、风险模型、成本曲线、scenario set、时间尺度和归一化方法均具有版本和 digest。目标分解必须进入 AllocationPlan，便于解释每个模块的边际贡献、成本与风险惩罚。

## Hard constraints

至少覆盖 gross/net/leverage/margin、strategy/capital-pool/account-book budget、factor/concentration/correlation/tail risk、liquidity/capacity/turnover、instrument/venue/session、borrow/funding、resource quota、module health 和最终 deterministic hard risk。缺失权威输入不能自动视为零使用量。

## Solver classes and truthful claims

- 凸/可证明问题可返回 `OPTIMAL` 或 `OPTIMAL_WITHIN_TOLERANCE`；
- 有限候选组合可声明“候选空间内最优”；
- 非凸问题只能返回 `BEST_KNOWN` 并报告 lower/upper bound 与 optimality gap；
- deadline 后只允许经独立 constraint validator 通过的 `FEASIBLE_FALLBACK`；
- 无可行、输入过期或数值失败不产生风险增加 plan。

“全局最优”永远是相对于指定模型、输入、候选空间和容差，不是对未来市场的绝对保证。

## Determinism and decomposition

固定 source/input bytes、solver build、policy、seed、canonical sort、tie-break 和 numeric policy 必须产生相同 SolverResult 和 AllocationPlan digest。并行求解的 reduction 顺序、浮点模式、线程数与随机数流必须被固定或在最终 canonical validator 中消除差异。

可使用 shadow price/拉格朗日分解协调局部模块，但中央层必须最终验证全局约束。局部 optimizer 不得因收到 shadow price 而获得资本 authority。

## Failure and recovery

solver crash/timeout、proposal loss、snapshot generation change、constraint validator disagreement 或 digest mismatch 均返回 typed failure。旧 plan 不得在 expiry 后继续使用。Allocator 重启更换 epoch；旧 epoch plan 全部拒绝。安全退出不依赖新优化结果。

结果语义见 [SolverResult V1](SOLVER-RESULT-CONTRACT.md)，输出见 [AllocationPlan V1](ALLOCATION-PLAN-CONTRACT.md)。
