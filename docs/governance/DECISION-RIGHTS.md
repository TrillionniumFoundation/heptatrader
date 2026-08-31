# 决策权与责任边界

Status: current normative
Applies to: architecture, contracts, modules, reviews and operations
Verification: `python3 scripts/check_documentation_control_plane.py` plus repository review rules
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

## 禁止事项

- 模块 owner 不能通过修改自身 manifest 扩大 authority。
- Strategy/Agent 团队不能把模型输出标记为 authoritative state。
- Management Control Plane 不能取得 Broker credential 或交易热路径写权限。
- Global Decision Plane 不能跳过 Execution 的最终风险与持久化。
- CI 不能自批准、自合并或用写权限修改自身 closure 状态。

紧急事故中，值班人员可以执行已预先授权的 kill/cancel/flatten，但不能临时扩大新风险能力。
