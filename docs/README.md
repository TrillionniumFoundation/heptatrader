# HeptaTrader documentation

本目录只把**当前代码可以支持的契约**标记为 current。设计草案、实验性 venue 和历史研究流程必须显式标注状态，不能与可运行能力混写。

## Current

| 文档 | 用途 |
|---|---|
| `AGENT-NATIVE-TRADING-OS-ARCHITECTURE.md` | 当前模型无关 Agent trading runtime 架构与权限边界 |
| `CAPABILITY-MATRIX.md` | 各 venue、运行模式、Agent 和研究能力的真实成熟度 |
| `ITERATION.md` | 本地开发与最小 PR gate 契约 |
| `OMS-EVENT-SCHEMA.md` | OMS journal/event 语义 |
| `RECONCILE-RULES.md` | uncertain recovery 与 authoritative reconciliation |
| `RISK-MODEL.md` | Simulator/IB PAPER 共用的确定性风险语义 |
| `RUNBOOK-INCIDENT.md` | 事故处置入口 |
| `RUNBOOK-KILLSWITCH.md` | kill switch 与安全退出 |
| `BROKER-NETWORK-ISOLATION.md` | broker 端口网络边界 |
| `CONFIGURATION.md` | 配置来源、profile lock 与 secret 注入 |
| `DEPLOYMENT.md` | 最小 runtime install 与 systemd 组装 |
| `OBSERVABILITY.md` | 当前运行时指标与 SLO |
| `SECURITY.md` | 当前安全边界与不变量 |

## Experimental

| 文档 | 状态 |
|---|---|
| `EURUSD-CONFIRMED-MOMENTUM-SHADOW-V2.md` | 现有 EURUSD 研究实现；只读 SHADOW，不代表通用研究平台 |
| `QMT-BRIDGE-MVP.md` | proposal/scaffold；真实 XT/QMT transport 尚未实现 |
| `QMT-SDK-REVIEW.md` | SDK 调研，不是运行时能力证明 |
| `XTQMT-VENUE-PLAN.md` | proposal |

## 文档规则

每份文档开头应包含：

```text
Status: current | experimental | proposal | deprecated
Applies to: <paths/components>
Last verified commit: <commit or moving-main>
```

current 文档中的命令和路径必须存在于当前仓库。失效的 release gate、PowerShell、round、P1 campaign、attestation、finalizer、witness 或已删除脚本不得继续出现在 current 文档中。
