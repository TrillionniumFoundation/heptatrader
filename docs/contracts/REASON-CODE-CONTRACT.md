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

复合响应必须在安全编码或 payload 降级之前，对调用方提供的原始候选 envelope 进行精确有界预检。已知的复合工具越界必须保留该工具的稳定专用 code；只有无法由生产者分类的编码或字段契约破坏才可降级为通用 `RESULT_ENVELOPE_INVALID`／`RESULT_ENVELOPE_UNCERTAIN`。审计记录与客户端收到的结果必须来自同一个规范化对象。

机器权威是 `verification/reason-code-registry-v1.json`。
