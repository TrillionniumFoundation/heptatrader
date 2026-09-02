# Development Traceability Model

Status: current normative
Applies to: capability, module, contract, test, gap, milestone and evidence registries
Verification: documentation-control-plane cross-reference checks and live qualification receipts
Authority: end-to-end development traceability

完整追踪链为：

```text
product capability
  -> providing/consuming modules
  -> versioned contracts and schemas
  -> source/build/deployment ownership
  -> module-specific generated technical guide and engineering coverage
  -> verification check IDs and fault/performance budgets
  -> gap/workstream/milestone repository implementation state
  -> exact source-head and merge-group integration evidence
  -> release artifact/SBOM/provenance identity
  -> protected external qualification
  -> deployment/runtime observation
```

## State scopes are not interchangeable

Registry 中的 `gap.state=closed` 或 milestone `state=closed` 只表示当前 repository tree 声称已经具备该实现、negative tests、canonical docs 和同树 evidence mapping。它不自动表示该 tree：

- 已合并到 default `main`；
- 已通过当前 exact source head 和 exact merge-group checks；
- 已取得 fresh non-author review；
- 已由 live no-bypass ruleset/merge queue 强制；
- 已生成或发布可重现 artifact；
- 已通过 IB PAPER 或其他真实环境资格；
- 已部署或正在安全运行；
- 获得 LIVE authority。

`milestone-registry-v1.json.policy.state_scope` 和每个 milestone 的 `integration_gate` 明确记录这一边界。生成的 roadmap 仍只展示 repository implementation state；它不是 GitHub、release、qualification 或 deployment 状态数据库。

## Evidence ladder

一个功能从代码存在到可运行能力，必须依次经过以下不降级阶段：

| Stage | Required evidence | What it may claim |
|---|---|---|
| `repository-implemented` | source + negative tests + canonical docs + registry links | implementation exists on that tree |
| `exact-head-verified` | required checks terminal-success on unchanged PR head | candidate head is internally verified |
| `independently-reviewed` | fresh non-author, domain-qualified review on same head | review acceptance for that exact head |
| `merge-group-verified` | same required contexts on exact merge queue revision | integration candidate verified |
| `merged-main` | protected merge receipt/no bypass | default-branch source truth |
| `artifact-reproducible` | dual clean build, install identity, SBOM/provenance | one exact distributable core candidate |
| `externally-qualified` | protected environment + real system evidence | exact artifact/config/environment qualification only |
| `deployed-observed` | startup/readiness/reconcile/monitoring evidence | that deployment is currently observed ready |

高阶段证据必须包含低阶段 exact identity；不能用历史结果、不同 SHA、不同 config、不同 binary、不同 SDK/harness、截图或手写 JSON补链。

## Capability derivation

任何 capability 如果缺少 module、contract、verification 或 maturity/qualification 映射，只能是 `planned` 或 `unsupported`。Capability 的有效状态取以下最小值：

```text
registered declared state
∩ exact revision implementation evidence
∩ release artifact capability ceiling
∩ environment qualification
∩ current deployment/readiness observation
```

例如：

- Simulator core 即使 repository implementation 为 `implemented`，在未合并/未发布时仍只是 candidate；
- IB PAPER 即使代码、workflow、mock tests 完整，也保持 `conditional`，直到同一 exact artifact/config/official SDK/harness/host/session 的受保护 receipt；
- PAPER receipt 永不推导 LIVE；
- CTP/XT negative stub 永不推导 venue availability。

## Module and document traceability

任何 current module 如果没有 owner、backup、state/concurrency/failure/resource contract，或者没有可追踪到真实源文件、构建目标和验证 ID 的完整技术指南，不得作为独立团队交付面。Guide 的章节存在不是充分条件；semantic documentation tests 还验证 manifest/profile 的关键工程内容确实进入生成文档，且没有 TODO/TBD 占位。

Source-size、ownership、build-target 和 migration exception 各自有独立生命周期。Functional gap 关闭后不能继续被滥用为永久豁免；accepted no-growth debt 使用唯一 `TD-SIZE-*`、owner、exit 和 review date，并在增长或低于阈值后自动失败。

## Live truth and immutable evidence

以下对象不得由 checked-in prose或 PR body伪造：

- current head/base/merge-group SHA；
- workflow check conclusion；
- review author/state/time；
- organization team member/maintainer/permission；
- ruleset bypass actors；
- CODEOWNERS parse/coverage；
- release/tag/attestation；
- protected environment approval；
- real Broker/PAPER callback、account/session和host identity。

这些状态由 GitHub API、release evidence、qualification verifier 和 runtime evidence读取。Repository JSON 可以定义期望政策和 verifier，但不能把期望描述为已安装事实。

## Naming and references

生成视图只展示注册表结果，不创建新状态。PR 描述、issue、dashboard、incident、release note 和 qualification receipt 必须引用同一 module/contract/capability/gap/check/reason code ID，不能发明平行命名。Mutable head/check结果不写死在长期 normative 文档；receipt 必须绑定 exact identity 和原始 API/evidence digest。
