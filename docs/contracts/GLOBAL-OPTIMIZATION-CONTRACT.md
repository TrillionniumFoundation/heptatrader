# 全局优化契约 V1

Status: current target contract
Applies to: proposal aggregator, global allocator, portfolio and risk
Verification: optimizer determinism, constraint and shadow tests
Authority: global optimizer authority

在一个明确的 authoritative snapshot 与 proposal set 上求解：

\[
\max_x \sum_m w_m U_m(x_m)
  - \lambda_R R(x)
  - C(x-x_{current})
  - \lambda_T T(x)
  - \lambda_Q Q(x)
\]

约束至少覆盖 gross/net/leverage/margin、strategy/capital-pool/account-book budget、factor/concentration/tail-risk、liquidity/capacity/turnover、venue/session/instrument、resource quota 和 deterministic hard risk。

| 状态 | 语义 |
|---|---|
| `OPTIMAL` | 满足声明模型的证明条件并由 solver 证明 |
| `OPTIMAL_WITHIN_TOLERANCE` | 在版本化容差内 |
| `BEST_KNOWN` | 非凸问题的最佳已知可行解，必须报告 bound/gap |
| `FEASIBLE_FALLBACK` | 超时或退化后的受限可行解 |
| `NO_FEASIBLE_PLAN` | 不存在可行方案 |
| `STALE_INPUT` | 输入过期或 generation 改变 |
| `NUMERIC_FAILURE` | 溢出、NaN/Inf 或 solver 数值失败 |

固定 input bytes、policy、solver build、seed、排序、tie-break 和 numeric policy 必须产生相同 canonical AllocationPlan digest。文档和 UI 不得把后三种可行状态宣传为“已证明全局最优”。
