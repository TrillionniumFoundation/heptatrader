# Hepta Capability Matrix

Status: generated current view
Applies to: repository-wide capability claims
Verification: `python3 scripts/generate_documentation_views.py --check`
Authority: generated from capability-registry-v2.json

> 本文件由机器注册表确定性生成。请修改注册表，不要直接修改本文件。

| Capability | State | Simulator | PAPER | LIVE | Release |
|---|---|---|---|---|---|
| `hepta.data.feature-plane` — Shard-aware market-data and feature plane | **planned** | planned | absent | forbidden | excluded |
| `hepta.execution.authority` — Execution authority, OMS, journal and recovery | **implemented** | active | experimental | forbidden | core |
| `hepta.gateway.typed-local-tools` — Typed local Tool Gateway and clients | **implemented** | active | experimental | forbidden | core |
| `hepta.global.multi-agent-allocation` — Global multi-Agent capital allocation | **planned** | planned-shadow | absent | forbidden | excluded |
| `hepta.intent.target-position` — Generation-bound target-position preview/apply | **implemented** | active | experimental | forbidden | core |
| `hepta.management.module-lifecycle` — Module registry, lifecycle and rollout control plane | **planned** | planned | absent | forbidden | excluded |
| `hepta.portfolio.compiler` — Deterministic multi-strategy portfolio compiler | **implemented** | library-boundary | absent | forbidden | core |
| `hepta.research.replay` — Capability-free deterministic research and replay | **experimental** | offline | no-mutation | forbidden | core-tools |
| `hepta.simulator.deterministic` — Deterministic simulator | **implemented** | active | not-applicable | not-applicable | core |
| `hepta.venue.ctp` — CTP venue execution | **unsupported** | not-applicable | forbidden | forbidden | excluded |
| `hepta.venue.ib-paper` — Interactive Brokers PAPER execution | **conditional** | not-applicable | conditional | forbidden | optional-qualified-only |
| `hepta.venue.live` — Any LIVE execution capability | **unsupported** | not-applicable | not-applicable | forbidden | excluded |
| `hepta.venue.xt` — XT / MiniQMT venue execution | **unsupported** | not-applicable | forbidden | forbidden | excluded |

状态是声明上限；实际可用性不得超过 exact-revision evidence 与 qualification。
