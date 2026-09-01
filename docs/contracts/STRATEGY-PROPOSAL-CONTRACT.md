# StrategyProposal V1

Status: current normative
Applies to: strategy modules and Global Decision intake
Verification: schema, canonicalization, completeness, lifetime intersection and digest tests
Authority: strategy-output contract

策略只能输出有界 `StrategyProposal`，不能持有 broker credential 或 mutation authority。Proposal 绑定 module/version、单调 sequence、capital pool/account book、authoritative snapshot digest、`valid_from_ms`、`expires_at_ms`、`horizon_ms` 与固定点 numeric policy。

每个 proposal 含 1–256 个互斥 candidate；candidate 具有稳定 ID、fixed-point utility 与按 instrument 排序的 target vector。重复 module/candidate/instrument、尚未生效或过期 proposal、snapshot 不一致、digest 不匹配和越界数值全部 fail closed。Seal 操作规范化顺序并生成 SHA-256；相同语义输入产生相同 digest。机器 schema 为 `schemas/strategy-proposal-v1.json`。

## ProposalSet completeness and lifetime

Global Decision 只能从调用方声明的完整 expected-module set 构造 `ProposalSet`。每个 expected module 必须恰有一个有效 proposal；未知 module、缺失 module、重复 module、重复 proposal ID、不同 capital pool、不同 account book 或不同 snapshot digest 均拒绝。

`ProposalSetBuilder` 接收一个确定的 decision capture time 与 authoritative `snapshot_valid_until_ms`。集合的有效窗口按以下规则计算：

```text
captured_at_ms = decision capture time
valid_from_ms = max(captured_at_ms, every member valid_from_ms)
valid_until_ms = min(
  snapshot_valid_until_ms,
  every member expires_at_ms,
  captured_at_ms + every member horizon_ms
)
```

加法必须检查溢出，且最终必须满足 `valid_from_ms <= captured_at_ms < valid_until_ms <= snapshot_valid_until_ms`。因此后续 allocator 不能延长任一成员或 authoritative snapshot 的寿命，也不能把旧 proposal set 重放到新的 capture time。ProposalSet digest 绑定 capture/validity/snapshot ceiling、book identity 以及排序后的 member proposal digest。
