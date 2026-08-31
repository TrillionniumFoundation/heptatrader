# StrategyProposal V1

Status: current target contract
Applies to: strategy runtime, proposal aggregator, global allocator and portfolio compiler
Verification: schema, canonicalization, expiry, property and replay tests
Authority: strategy-to-global-decision contract

`StrategyProposal` 表达某策略在一个明确 point-in-time 输入和有效期内的可行偏好集合。它不是订单、最终目标仓位、资本授权或风险批准。

## Identity and envelope

必需字段包括 proposal ID、module ID/version/instance、owner、capital pool、account book、module sequence、input/model/config digest、snapshot reference、valid-from、expiry、horizon 和 numeric policy。相同 module/shard 的 sequence 单调；相同 proposal ID + digest 幂等，不同 digest 冲突拒绝。

## Economic content

策略至少输出一种可由中央层比较的表达：

1. 有限 Pareto candidates；
2. 有界 feasible region + utility function family；
3. target/size grid + expected utility curve。

每个候选必须包含 target vector、expected return/utility、uncertainty/confidence、factor exposure、liquidity/capacity、transaction-cost curve、turnover sensitivity、horizon 和资源需求。单位、币种、价格基准、概率/置信定义和时间尺度必须版本化。

## Validity and completeness

proposal 只有在 snapshot/instrument universe、feature generation、calendar、FX conversion、cost model 和 risk-factor mapping 完整时可参与优化。过期、future observation、generation mismatch、非 finite、未知单位、空候选、越界数组或 digest 不符时返回稳定拒绝码。

ProposalAggregator 对每个 capital pool 建立 canonical proposal set：按 module、account book、horizon、instrument 和 proposal ID 排序；同一 owner 的替代/撤回语义必须显式。缺少 required module 的 set 不得默认为“零观点”，除非 capital policy 明确允许降级并记录原因。

## Forbidden authority

proposal 不得包含或覆盖 authoritative current position、cash/PnL、Broker order ID、venue ACK、credential、session token、preview permit、final risk decision、allocator weight 或 mutation capability。策略可以收紧自身 bounds，不能扩大中央预算。

机器 schema 为 `schemas/strategy-proposal-v1.json`。
