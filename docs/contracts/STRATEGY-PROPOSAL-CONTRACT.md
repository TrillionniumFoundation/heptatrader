# StrategyProposal V1

Status: current normative
Applies to: strategy modules and Global Decision intake
Verification: schema, canonicalization, completeness and digest CTests
Authority: strategy-output contract

策略只能输出有界 `StrategyProposal`，不能持有 broker credential 或 mutation authority。Proposal 绑定 module/version、单调 sequence、capital pool/account book、authoritative snapshot digest、validity window 与固定点 numeric policy。

每个 proposal 含 1–256 个互斥 candidate；candidate 具有稳定 ID、fixed-point utility 与按 instrument 排序的 target vector。重复 module/candidate/instrument、过期 proposal、snapshot 不一致、digest 不匹配和越界数值全部 fail closed。Seal 操作规范化顺序并生成SHA-256；相同语义输入产生相同 digest。机器 schema 为 `schemas/strategy-proposal-v1.json`。

## ProposalSet lifetime intersection

`ProposalSet` records capture time, effective valid-from, effective valid-until and authoritative snapshot expiry. Its start is the maximum member start. Its end is the minimum of every member expiry, every capture-plus-horizon bound and snapshot expiry. Arithmetic overflow, a future start, an empty interval, an expired member or an interval beyond the snapshot is rejected before allocation.

## 2.3.1 intake bounds and expiry correction

`StrategyProposalContract::ValidateAndSeal` treats the effective input window as
half-open: `validFromMs <= nowMs < expiresAtMs`. At the exact expiry timestamp it
returns `PROPOSAL_NOT_CURRENT`, even with a matching claimed digest. This matches
the downstream allocation validity boundary; a digest cannot extend a lifetime.
No subtraction or expiry addition is required to perform this boundary check.

Before copying or sorting the proposal, admission checks all current body bounds:
1..256 candidates; canonical candidate IDs of at most 128 bytes and utilities
within the fixed numeric range; 1..256 targets per candidate; at most 4,096 targets
in total; canonical instrument IDs of at most 128 bytes and in-range signed target
values. A supplied nonempty proposal digest must have the canonical SHA-256 shape.
The aggregate limit is checked with remaining-capacity subtraction before addition.
Malformed bodies must not cause a deep normalization copy before being rejected.

This non-allocating body preflight does not replace canonical duplicate detection
or digest verification. Existing normalization, duplicate candidate/instrument
rejection and actual digest comparison still run on the bounded copy. Successful
in-window canonical values retain the same digest encoding. For inputs with more
than one defect, no invariant is promised about which rejection is selected first.
The existing `PROPOSAL_DIGEST_MISMATCH` also covers malformed supplied digests.

The guarantee concerns work inside ValidateAndSeal. It cannot undo memory already
allocated by a caller/transport, cap arbitrary callers of the low-level `Digest`
serialization helper, authenticate an issuer, enforce global memory quotas or
establish sequence monotonicity across calls. Those require separate admission
and supervisor boundaries; the normalized proposal remains content, not authority.

`tests/strategy_proposal_admission_tests.cpp`, run by
`tests/python/test_strategy_proposal_admission.py`, contains five always-active
regression functions. They check pre-copy rejection of oversized candidate IDs,
instrument IDs, target vectors, aggregate targets and supplied digest strings;
exact 256-candidate/4,096-target/128-byte-ID bounds; both timestamp endpoints and
UINT64_MAX; canonical order, duplicate/digest failures; and every observed
allocation-failure ordinal while preserving the caller's input and empty rejected
output. A test-only allocator records allocation size/total after input creation:
rejection is required to stay below a small fixed diagnostic budget, not scale
with the malformed body. This is allocation-behavior evidence, not a total-process
RSS or deployment latency guarantee.
