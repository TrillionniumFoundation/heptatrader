# 证据模型

Status: current normative
Applies to: CI, release, qualification and derived status
Verification: `docs/verification/evidence-index-schema-v1.json`
Authority: dynamic evidence authority

每个 evidence index 绑定 exact `git_sha`、可选 merge candidate SHA、module/contract/capability test result、benchmark fixture/baseline、artifact manifest/SBOM/provenance、external qualification identity 和 derived gap/milestone state。

Evidence 只能由执行实际命令的受控 workflow/harness 产生。生成时间、工具链、输入 digest 和结果 digest 必须可审计。

规范文档不记录某个临时 SHA 是否绿色。UI、PR summary、release notes 和 dashboard 从 evidence index 生成，以消除 PLAN、PR body、CI 和历史文档四分裂。
