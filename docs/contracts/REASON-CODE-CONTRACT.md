# Reason Code Contract

Status: current normative
Applies to: all typed decisions, rejections, alerts and evidence
Verification: `docs/verification/reason-code-registry-v1.json` and registry checks
Authority: stable machine reason-code authority

每个权威决策必须返回有限、稳定、可测试的 reason code。自由文本仅用于有界诊断，不得成为自动化分支条件。

- code 在同一 major contract 内 append-only；删除、重命名或改变语义需要 major version；
- code 由唯一模块 family owner 管理；
- unknown code 在风险/权限边界 fail closed；
- metric label 只能使用注册 code 或 `redacted/unknown`；
- credential、account、token、prompt、路径和任意用户输入不得拼入 code；
- 每个新增 code 必须映射测试、严重级别、默认动作和文档。

机器权威是 `verification/reason-code-registry-v1.json`。
