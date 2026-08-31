# Team Topology for Modular Development

Status: current normative
Applies to: module ownership, reviews, on-call and cross-module delivery
Verification: module registry owners, CODEOWNERS and review-policy checks
Authority: team-scale development topology

每名工程师有一个 primary module，但关键模块不能只有一个人理解。每个 ModuleManifest 定义 DRI team、backup team 和至少一个跨域 reviewer。

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

跨模块变更先修改 contract/registry，再由各 owner 修改实现。禁止一人通过“大型全局 PR”绕过 consumer review。on-call 与 incident ownership 由 deployment module 映射，不按文件最近提交者临时决定。
