# Team Topology for Modular Development

Status: current normative
Applies to: module ownership, reviews, on-call and cross-module delivery
Verification: module registry owners, GitHub team mapping and `G-TEAM-001` live governance evidence
Authority: team-scale development topology

每名工程师有一个 primary module，但关键模块不能只有一个人理解。每个 ModuleManifest 定义 DRI team、backup team 和至少一个跨域 reviewer。

## Logical ownership versus enforced ownership

ModuleManifest 中的 `@hepta/...` handle 表示逻辑职责，不自动证明相应 GitHub team、CODEOWNERS、branch ruleset 或 required review 已存在。逻辑 owner 到真实组织 team 的唯一映射由 [`../../.github/github-team-mapping-v1.json`](../../.github/github-team-mapping-v1.json) 定义；[`../../.github/CODEOWNERS.team-template`](../../.github/CODEOWNERS.team-template) 是该映射的确定性生成结果，在团队实际建立前不具有 CODEOWNER 权威。

只有在以下对象被同一仓库、同一 exact revision 的机器证据验证后，ownership 才从 logical 升级为 enforced：

```text
GitHub team identity + members + maintainers + repository write permission
+ active team-only .github/CODEOWNERS path coverage
+ active no-bypass default-branch ruleset
+ one required context set on pull_request and merge_group
+ merge queue exact-candidate success
+ fresh non-author exact-head approval
+ read-only API verification receipt
```

在这些条件完成前，`G-TEAM-001` 保持开放；文档、PR、管理员权限或静态模板不得把建议团队拓扑描述为已启用的平台强制控制。

## Canonical organization teams

[`../../.github/github-team-mapping-v1.json`](../../.github/github-team-mapping-v1.json) 当前定义八个目标团队：

- `architecture-contracts`：Architecture、Contracts、Numeric；
- `marketdata-feature`：Market Data、Feature；
- `strategy-runtime`：Strategy Runtime；
- `portfolio-risk`：Global Allocation、Portfolio、Risk；
- `execution-oms`：Execution、State、OMS、Reconciliation；
- `venue-adapters`：Simulator、IB 与未来经独立审查的 adapter；
- `gateway-session-security`：Agent Support、Gateway、Session、SDK/MCP、Security Runtime；
- `reliability-operations-research`：Documentation、Reliability、Delivery、Operations、Research Validation。

每个团队至少有两名成员和一名 maintainer，并拥有仓库 write、maintain 或 admin permission。个人用户不能替代 team CODEOWNER；secret team 不能作为可验证的公开 CODEOWNER。

## Platform installation sequence

1. 组织 owner 按 team mapping 创建八个真实 GitHub teams，并配置成员、maintainer 和仓库权限。
2. 在独立 PR 中把 `.github/CODEOWNERS` 替换为 `CODEOWNERS.team-template` 的精确内容；不得手工删减跨域 reviewer。
3. 按 [`../../.github/github-governance-policy-v1.json`](../../.github/github-governance-policy-v1.json) 创建一个针对 default branch 的 active ruleset，且 `bypass_actors` 必须为空。
4. ruleset 使用 [`../../.github/required-check-contexts-v1.json`](../../.github/required-check-contexts-v1.json) 的同一组 required contexts；每个 context 必须在 `pull_request` 与 `merge_group` 上由同一唯一 job 报告。
5. 启用 merge queue、stale-review dismissal、CODEOWNER review、last-push approval、至少两名批准者和 review-thread resolution。
6. 创建受保护的 `repository-governance` environment，仅向独立 verifier 提供只读 `HEPTA_GOVERNANCE_TOKEN`；token 必须能读取 ruleset bypass、teams、CODEOWNERS、reviews 和 checks，但不能写仓库。
7. 最终 PR 进入 Ready 状态并取得 fresh non-author exact-head approval 后进入 merge queue；所有 required contexts 在 exact merge-group revision 上重新成功。
8. 从 exact candidate 运行 `GitHub Governance Qualification`，由 `scripts/verify_github_governance.py` 生成 digest-bound、0600、API-response-bound receipt。

任何步骤缺失、API 字段不可见、required check 被 skip/cancel、审批绑定旧 SHA、team 无权限或 ruleset 存在 bypass 时，验证必须失败，`G-TEAM-001` 不得关闭。

## Review matrix

- module-internal implementation：DRI 或 backup 1 approval；
- public contract/schema：provider + consumer + contract reviewer；
- state/authority/risk/journal/fencing：execution-safety + owning teams；
- credential/network/release/qualification：security + operations + independent approver；
- Constitution/LIVE：A3/O4 governance。

跨模块变更先修改 contract/registry，再由各 owner 修改实现。禁止一人通过大型全局 PR 绕过 consumer review。on-call 与 incident ownership 由 deployment module 映射，不按文件最近提交者临时决定。
