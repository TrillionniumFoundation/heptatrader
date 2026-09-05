# Capability Maturity Model

Status: current normative
Applies to: capability registry, product claims, release and qualification
Verification: capability registry cross-reference and evidence derivation
Authority: maturity semantics

| State | Minimum meaning |
|---|---|
| `unsupported` | API absent or typed fail-closed; no outbound side effect |
| `prototype` | isolated experimental code; no active install/capability claim |
| `experimental` | implementation exists with partial integration; limitations explicit |
| `conditional` | repository implementation exists but exact external dependency/qualification required |
| `implemented` | contract, code, negative tests, runtime discovery, install and exact-revision core evidence agree |
| `qualified` | implemented plus exact artifact/config/environment fault and operational qualification |
| `deprecated` | current replacement exists; no new capability, removal window declared |

状态是多维字段的派生上限：design、implementation、build、integration、verification、qualification、release 和 operations。单一绿色单测不能提升系统能力；外部 qualification 不能提升不同 SHA、binary、config 或环境。

能力降级可由新失败、stale evidence、dependency change、security finding 或 qualification identity 变化自动触发；降级不需要等待宣传文本更新。LIVE 默认 `unsupported`。
