# 决策权与责任边界

Status: current normative
Applies to: architecture, contracts, modules, reviews and operations
Verification: documentation controls plus live repository-governance qualification
Authority: review and authority allocation

## 决策域

| 决策 | 最终权威 | 必须参与 |
|---|---|---|
| 产品边界与能力声明 | Product/Architecture Council | capability owner、security |
| 宪章、信任边界、authority 迁移 | Architecture Council | execution-safety、risk、安全 |
| public contract/schema | Contract owner | producer、consumer、compatibility reviewer |
| 模块内部实现 | Module DRI team | backup reviewer |
| 风险规则和 reason code | Risk authority | state authority、Execution |
| Broker mutation 和恢复语义 | Execution authority | OMS、adapter、reconcile |
| 全局目标函数和资本政策 | Portfolio/Risk authority | strategy、Execution、research |
| release 与 qualification | Release/Operations authority | security、independent approver |
| LIVE 激活 | 独立 O4 决策 | security、operations、risk、legal/compliance |

## Repository enforcement

逻辑职责只由 ModuleManifest 描述；平台强制权威由以下对象共同建立：

- `.github/github-team-mapping-v1.json`：把全部 manifest DRI、backup、reviewer 唯一映射到真实组织 team；
- `.github/CODEOWNERS`：只允许 `@TrillionniumFoundation/<team-slug>`，不得以个人 bootstrap 作为最终状态；
- `.github/github-governance-policy-v1.json`：规定 default branch、无 bypass ruleset、审批、required checks 和 merge queue；
- `.github/required-check-contexts-v1.json`：规定 PR 与 merge-group 必须执行的同一组稳定 context；
- `scripts/verify_github_governance.py`：只读读取 live GitHub API，并验证 teams、permissions、CODEOWNERS、ruleset、reviews 与 exact-revision checks；
- `GitHub Governance Qualification`：在受保护 environment 中对一个指定 PR head 与 merge-group SHA 生成不可伪造的摘要 receipt。

只有 live verifier 成功并且 receipt 绑定最终不变 head、exact merge-group revision 与 API response digests 时，团队和合并治理才构成 evidence。静态 JSON、模板、管理员截图、PR 描述或手工 success status 均无决策权。

## Required-check semantics

同一个 ruleset required context 必须由唯一 job 产生，并在 `pull_request` 与 `merge_group` 事件上使用相同名称实际执行。PR 检查不能替代 merge-group 检查；merge-group required workflow 不得被 concurrency cancellation 取消。任何 required job 的 `skipped`、`cancelled`、`timed_out`、缺失或非最新失败重跑都不构成批准。

ruleset 必须要求 stale approval dismissal、CODEOWNER approval、last-push approval、至少两名批准者、线程解决和 squash merge，并且 `bypass_actors` 为空。管理员权限不构成例外。

## 禁止事项

- 模块 owner 不能通过修改自身 manifest 扩大 authority。
- Strategy/Agent 团队不能把模型输出标记为 authoritative state。
- Management Control Plane 不能取得 Broker credential 或交易热路径写权限。
- Global Decision Plane 不能跳过 Execution 的最终风险与持久化。
- CI 不能自批准、自合并或用写权限修改自身 closure 状态。
- 组织管理员不能通过 bypass、直接 push、手工 status 或跳过 merge queue 关闭 `G-TEAM-001`。
- 外部 qualification verifier 只能读取证据，不能创建 team、修改 ruleset、批准 PR 或合并代码。

紧急事故中，值班人员可以执行已预先授权的 kill/cancel/flatten，但不能临时扩大新风险能力。
