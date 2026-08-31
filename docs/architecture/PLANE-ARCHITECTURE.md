# 六平面目标架构

Status: current target contract
Applies to: future modular runtime and current migration decisions
Verification: `docs/program/milestone-registry-v1.json` and architecture verification gates
Authority: plane architecture authority

## 1. Research and Replay Plane

管理 point-in-time data、feature/label、成本、回放和策略验证。输出版本化 artifact 或 StrategyProposal 草案，不授予 mutation capability。

## 2. Market Data and Feature Plane

接收 venue/feed，完成 normalization、symbol/calendar/time、feature graph 和 immutable snapshot。高频数据走 shard-aware data plane，不经过 Tool Gateway 控制调用队列。

## 3. Agent and Strategy Plane

运行彼此隔离的策略模块。模块只读取有界数据并输出 `StrategyProposal`；单 Agent 兼容路径可输出受限 target-position intent。

## 4. Global Decision Plane

聚合 proposal，进行资本分配、跨策略净额、组合优化和组合级风险，生成版本化 `AllocationPlan`。逻辑集中，按 capital pool / account / risk book 物理分片。

## 5. Execution Authority Plane

负责最终 risk、permit、OMS、journal、state、reconciliation、execution planning、venue adapter 和 safe exit。它是唯一 mutation authority。

## 6. Management Control Plane

负责 module registry、版本、配置、资源配额、health、shadow/canary/rollback 和 lifecycle。它不参与每个 tick，也不持有 Broker credential。

```text
management -> lifecycle/policy only
data -> strategy -> proposal -> global decision -> intent -> execution -> venue
venue events -> OMS/state -> authoritative snapshot -> readers
research artifact -> reviewed strategy input only
```

任何跨越 Execution 直达 venue 的第二路径都属于宪章违规。
