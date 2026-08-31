# StrategyProposal V1

Status: current target contract
Applies to: strategy modules, proposal aggregator and global allocator
Verification: proposal schema, property, expiry and replay tests
Authority: strategy-to-global-decision contract

`StrategyProposal` 表达策略在给定输入下的可行偏好，不是订单或最终目标。

必需字段：

```text
proposal_id
module_id / module_version
model/config/input digests
owner / capital_pool / account_book
snapshot reference and proposal sequence
valid_from_ms / expires_at_ms / horizon_ms
candidate target vectors or feasible region
expected utility / return surface
uncertainty / confidence
factor exposures
capacity / liquidity bounds
transaction-cost curve
turnover sensitivity
resource demand
numeric_policy_version
```

proposal 必须 immutable、bounded、canonical、可哈希。同一 module/shard 的 sequence 单调；重复 ID、相同 digest 幂等。过期、generation mismatch、非 finite、单位未知或候选空间为空时拒绝。模块可提交多个 Pareto candidates，让中央层在全局约束下选择。

proposal 不包含 authoritative current position、Broker order ID、credential、venue ACK、permit 或 final risk result。
