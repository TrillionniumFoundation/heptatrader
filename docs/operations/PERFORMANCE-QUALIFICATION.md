# Performance Qualification

Status: current normative
Applies to: runtime latency, throughput, queue and host-tuning claims
Verification: same-fixture performance budgets and target-host observations
Authority: performance-claim policy

性能声明必须绑定 source、compiler/toolchain、build type、fixture、hardware/VM、CPU governor、affinity、queue load 和 sample distribution。只报告平均值不足以支持交易运行时声明，至少记录 p50/p95/p99/p999、max、drop/backpressure 和 CPU/memory。

Host tuning 不能替代正确性、journal、risk 或 qualification。baseline 更新需要解释变化、比较原始分布并由模块 owner 与 reliability reviewer批准。任何优化导致安全测试、determinism 或 emergency-lane 性能退化均拒绝。
