# 团队拓扑与模块责任

Status: current target governance
Applies to: engineering organization, CODEOWNERS and reviews
Verification: module registry ownership checks and repository rules
Authority: team topology authority

推荐约 40 人团队：

| Domain | 建议人数 |
|---|---:|
| Architecture / Contracts / Module Platform | 4 |
| Market Data / Feature Runtime | 6 |
| Strategy Modules | 10 |
| Global Optimizer / Portfolio / Risk | 6 |
| OMS / State / Execution / Venue | 9 |
| Security / Gateway / Reliability / Delivery | 5 |

每人有一个 primary module，但关键模块不得只有一个知识 owner。每个模块至少有 DRI team、backup team、contract reviewer、适用的 authority/safety reviewer，以及两名可批准紧急修复的成员。

跨模块变更先修改 contract，再由各模块 owner 更新实现。目标 CODEOWNERS 使用 GitHub teams，不以单个人作为全仓库默认审批单点。
