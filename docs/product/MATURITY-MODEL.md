# 能力成熟度模型

Status: current normative
Applies to: capability lifecycle and public claims
Verification: `docs/product/capability-registry-v2.json` plus generated evidence
Authority: capability maturity authority

能力成熟度是多维事实的派生结果，不是一个手填标签。

| 派生状态 | 最低语义 |
|---|---|
| Unsupported | 运行时拒绝或完全缺失 |
| Prototype | 仅局部代码/fixture，不进入 active capability discovery |
| Experimental | contract 与代码存在，但 integration/reliability 不完整 |
| Conditional | 代码完整，依赖受控外部环境或资格认证 |
| Implemented | 默认或显式构建、完整契约、负向测试、E2E 和 same-revision evidence |
| Qualified | Implemented 且特定环境、artifact、配置完成资格认证 |
| Deprecated | 仍承担兼容责任，但禁止扩展 |

`Implemented` 至少要求 design、implementation、module ownership、contract、unit、negative、E2E、install/discovery 和 same-revision CI 一致。

`Qualified` 还要求 exact source SHA、binary/artifact digest、configuration identity、external environment identity、完整 fault scenarios、deployment/incident/rollback runbook 以及有效期和失效条件。

capability registry 保存结构化事实；CI evidence 生成最终状态。
