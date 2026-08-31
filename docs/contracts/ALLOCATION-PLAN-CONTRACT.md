# AllocationPlan V1

Status: current target contract
Applies to: global decision output, portfolio compiler and Execution intake
Verification: plan canonicalization, expiry, replay and risk-revalidation tests
Authority: global-decision output contract

`AllocationPlan` 是 Global Decision Plane 的 immutable 输出，不是 Broker command。

必需内容：

```text
plan_id
capital_pool / account_book / allocator_epoch
objective_version / solver_version / policy_version
input proposal-set digest
snapshot vector/generation/digest
accepted and rejected proposals with reason codes
per-strategy allocations
net instrument targets
constraints and remaining slack
shadow prices
objective decomposition
optimality_status / bound / gap
fallback mode
created_at_ms / valid_until_ms
numeric_policy_version
plan_digest
```

只能由声明的 account/book authority 应用。plan 过期、allocator epoch/fence 改变、snapshot 改变或 digest 不匹配时拒绝。同一 plan 重复提交幂等；不同 plan 不得复用 permit。target/delta 必须经过 PortfolioCompiler、deterministic risk、preview permit、journal 和 Execution revalidation。空计划返回 typed no-op。
