# StrategyProposal V1

Status: current normative
Applies to: strategy modules and Global Decision intake
Verification: schema, canonicalization, completeness and digest CTests
Authority: strategy-output contract

策略只能输出有界 `StrategyProposal`，不能持有 broker credential 或 mutation authority。Proposal 绑定 module/version、单调 sequence、capital pool/account book、authoritative snapshot digest、validity window 与固定点 numeric policy。

每个 proposal 含 1–256 个互斥 candidate；candidate 具有稳定 ID、fixed-point utility 与按 instrument 排序的 target vector。重复 module/candidate/instrument、过期 proposal、snapshot 不一致、digest 不匹配和越界数值全部 fail closed。Seal 操作规范化顺序并生成SHA-256；相同语义输入产生相同 digest。机器 schema 为 `schemas/strategy-proposal-v1.json`。

## ProposalSet lifetime intersection

`ProposalSet` records capture time, effective valid-from, effective valid-until and authoritative snapshot expiry. Its start is the maximum member start. Its end is the minimum of every member expiry, every capture-plus-horizon bound and snapshot expiry. Arithmetic overflow, a future start, an empty interval, an expired member or an interval beyond the snapshot is rejected before allocation.
