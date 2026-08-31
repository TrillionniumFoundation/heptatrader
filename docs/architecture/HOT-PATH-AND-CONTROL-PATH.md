# Hot Path and Control Path Boundary

Status: current target contract
Applies to: market data, feature, strategy, allocation, Gateway and Execution IPC
Verification: queue, latency, backpressure and dependency gates
Authority: runtime-path classification authority

## Control path

身份、session、capability、配置、module lifecycle、health、tool discovery 和 operator commands 使用 typed bounded control IPC。控制面允许较高语义验证成本，但必须有 deadline、queue limit 和稳定错误码。

## Hot data path

行情、feature update、proposal、allocation input 和 venue callback 不应通过一个全局 Gateway mutex/queue。目标实现按 venue/instrument、module instance、capital pool/account book 和 execution domain 分片，使用有界 SPSC/MPSC/ring-buffer 或等价机制。

## Mandatory rules

- market/feature 可 coalesce 旧值，但必须显式暴露 sequence gap 和 freshness；
- StrategyProposal 以 expiry 限制积压，过期不参与优化；
- AllocationPlan、OMS command 和 authoritative event 不得静默丢失；
- cancel/reduce/flatten 使用独立 emergency lane；
- telemetry 使用 per-thread/per-shard aggregation，不在热路径竞争全局锁；
- 控制面故障不得改变 Execution 的既有安全退出能力。

每个模块 manifest 必须把接口标为 `control`、`hot-data`、`durable-authority` 或 `emergency`，并关联性能和 overflow policy。
