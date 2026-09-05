# Resource Scheduling and Admission

Status: current target contract
Applies to: strategy modules, feature shards, global allocator and execution domains
Verification: resource-budget, overload, fairness and emergency-lane tests
Authority: compute and queue resource policy

资源调度必须服从风险和实时性，不以“CPU 利用率最大化”为目标。

- Management 为模块分配 CPU、memory、thread、queue、I/O 和 proposal-rate quota。
- Strategy/feature 超预算时先降级、丢弃过期非权威工作或 quarantine，不得拖慢 Execution。
- allocator 使用 hard deadline；deadline 后只能返回已验证的 fallback 或 no-plan。
- Execution、journal、state projection、reconcile 和 emergency lane 具有保留资源，不能被 research/strategy 抢占。
- fairness 以 owner/capital-pool 为界；不得让单个模型造成 queue monopoly。
- admission decision 必须记录 bounded reason code 和当前预算 generation。

资源预算变更属于版本化 policy，不能由模块自身扩大。
