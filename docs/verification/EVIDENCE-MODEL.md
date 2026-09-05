# 证据模型

Status: current normative
Applies to: CI, pull requests, merge candidates, releases, qualification and derived status
Verification: `docs/verification/evidence-index-schema-v1.json`
Authority: dynamic evidence authority

每个 evidence index 必须绑定 exact source head、base identity、可选 merge-candidate SHA、workflow/run/job identity、toolchain、输入 digest、结果 digest、module/contract/capability test result、benchmark fixture/baseline、artifact manifest/SBOM/provenance、external qualification identity 和 derived gap/milestone state。

Evidence 只能由实际执行命令的受控只读 workflow 或受保护 qualification harness 产生。缺失、queued、in-progress、skipped、cancelled、timed-out 和 stale 都不是 success；局部 job 成功不能覆盖 required aggregate 的失败。

## Mutable PR state

PR 描述只保存稳定的目标、边界、change class、受影响 ID、迁移/回滚和所需 evidence ID。它不得硬编码“当前 head SHA”“当前 checks 已通过”或复制一份会随 push 过期的状态表。当前 head、base、merge candidate、review decision 和 workflow 结果以 GitHub live metadata 与其绑定的 evidence 为准。

每次 head 变化都会使此前 head 的 review、workflow 和本地输出失去当前性。更新说明文字不能把旧 evidence 重新绑定到新 head，也不能把 Draft 自动提升为 Ready。

规范文档不记录某个临时 SHA 是否绿色。UI、check summary、release notes 和 dashboard 从 evidence index 生成，以消除 PLAN、PR body、CI 和历史文档四分裂；Git 中不提交人工维护的 exact-head receipt。
