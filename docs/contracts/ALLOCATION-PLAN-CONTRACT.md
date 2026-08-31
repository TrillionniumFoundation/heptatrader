# AllocationPlan V1

Status: current target contract
Applies to: Global Decision output, PortfolioCompiler, target intent and Execution intake
Verification: schema, canonicalization, feasibility, expiry, replay and Execution-revalidation tests
Authority: global-decision output contract

`AllocationPlan` 是 immutable、bounded、可重放的全局策略分配结果，不是 Broker command，也不授予 mutation capability。

## Required identity

Plan 绑定 plan ID、capital pool/account book、allocator epoch/fence、objective/solver/policy version、proposal-set digest、snapshot vector/generation/digest、numeric policy、created/valid-until、canonical payload digest 和签发 authority。

## Decision body

- accepted/rejected proposal 及 reason code；
- per-strategy/per-module capital、risk、turnover 和 compute allocation；
- net instrument target 与 horizon/urgency bounds；
- hard constraint evaluation、remaining slack 和 active binding constraints；
- shadow prices/dual values（适用时）；
- expected utility、risk、cost、tail and resource objective decomposition；
- SolverResult status、bound、gap、termination reason 和 fallback class。

## Validation and application

Plan 在进入执行前依次经过：schema/canonical digest → issuer/epoch/fence → expiry → proposal/snapshot binding → independent hard-constraint validator → PortfolioCompiler/quantization → deterministic risk → preview permit → durable command/journal → Execution revalidation。

只能由声明的 account-book authority 应用。snapshot、policy、FX/quote generation 或 position 在计划后改变时，必须重新编译或重新优化；不得以客户端字段修补旧 plan。同一 plan 的相同 apply 幂等；不同 plan 不能复用 permit 或 command ID。空 target 集返回 typed no-op。

## Partial execution

Plan 不保证一次性成交。Execution 可以在不改变目标、风险和 cost bounds 的前提下拆单、路由、撤单和重规划；任何实质改变目标或约束都需要新 plan/intent。fills 和 residual position 反馈到下一 authoritative snapshot，不由策略自行估算。

机器 schema 为 `schemas/allocation-plan-v1.json`。
