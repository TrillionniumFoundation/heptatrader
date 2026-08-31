# Team Topology for Modular Development

Status: current normative
Applies to: module ownership, reviews, on-call and cross-module delivery
Verification: module registry owners and `G-TEAM-001` governance evidence
Authority: team-scale development topology

每名工程师有一个 primary module，但关键模块不能只有一个人理解。每个 ModuleManifest 定义 DRI team、backup team 和至少一个跨域 reviewer。

## Logical ownership versus enforced ownership

ModuleManifest 中的 `@hepta/...` handle 表示目标团队职责，不自动证明相应 GitHub team、CODEOWNERS、branch ruleset 或 required review 已存在。只有在以下对象被同一仓库证据验证后，ownership 才从 logical 升级为 enforced：

```text
GitHub team identity
+ .github/CODEOWNERS path coverage
+ branch/ruleset required-review policy
+ emergency backup approver
+ negative test proving protected changes cannot bypass review
```

在这些条件完成前，`G-TEAM-001` 保持开放；文档和 PR 不得把建议团队拓扑描述为已启用的平台强制控制。

## Suggested groups

- Architecture / Contracts / Numeric Platform
- Market Data / Feature Platform
- Strategy Runtime and Strategy Modules
- Global Allocation / Portfolio / Risk
- State / OMS / Execution / Reconciliation
- Venue IB / Simulator / future reviewed adapters
- Gateway / Session / SDK / MCP / Security Runtime
- Reliability / Delivery / Operations / Research Validation

## Review matrix

- module-internal implementation：DRI 或 backup 1 approval；
- public contract/schema：provider + consumer + contract reviewer；
- state/authority/risk/journal/fencing：execution-safety + owning teams；
- credential/network/release/qualification：security + operations + independent approver；
- Constitution/LIVE：A3/O4 governance。

跨模块变更先修改 contract/registry，再由各 owner 修改实现。禁止一人通过大型全局 PR 绕过 consumer review。on-call 与 incident ownership 由 deployment module 映射，不按文件最近提交者临时决定。
