# Performance Qualification

Status: current normative
Applies to: runtime latency, throughput, queue and host-tuning claims
Verification: performance budget registry, same-fixture executable gates and target-host observations
Authority: performance-claim policy

性能声明必须绑定 source、binary、compiler/toolchain、build type、fixture、hardware/VM、kernel、CPU governor、affinity、queue load、sample distribution 和 correctness result。只报告平均值不足以支持交易运行时声明，至少记录 p50/p95/p99/p999、max、sample count、drop/backpressure 和 CPU/memory。

## Budget states

`performance-budgets-v1.json` 每个 entry 明确为：

- `declared`：目标和允许回退比例已经定义，但缺少 canonical absolute baseline、representative fixture 或 target-host distribution；**不能支持性能、readiness、release 或 qualification 声明**；
- `implemented`：存在同一 revision 的 executable fixture、机器绑定 threshold/complexity contract 和可记录 source/build/toolchain identity。

当前只有：

- `risk-policy-v1`：implemented；
- `global-allocator-v1`：implemented；
- Gateway、snapshot、portfolio compiler 和 execution durable-to-send：declared target。

文档、roadmap 或 capability registry 不能把 declared budget 描述为已经达到 SLO。

## Canonical risk fixture

风险性能权威只有 `benchmarks/core-latency-baseline-v1.json`。Top-level CMake 使用 `string(JSON ...)` 读取 `fixture`、`p99_microseconds` 和 `maximum_regression_percent`，生成 `heptatrader_performance_budget.h`；C++ fixture 不保留另一套手写阈值。

`hepta_risk_latency_fixture_tests`：

1. 使用 exact fixed-point risk path；
2. 预热 500 次；
3. 收集 10,000 个 steady-clock 样本；
4. 输出 p50/p95/p99/p999/max、sample count、numeric policy、baseline 和 allowed threshold；
5. p99 超过机器基线 + 10% 时失败。

Hosted CI 的数值只用于同 fixture/toolchain 回归门禁，不是生产 target-host 延迟承诺。改变 baseline 必须提交原始分布、原因、correctness结果，并由 risk owner 与 reliability reviewer独立批准；不能通过放宽百分比修复回归。

## Global allocator budget

Global allocator 的 implemented budget 当前是 bounded complexity/deadline contract，而非通用 wall-clock SLA。Evidence 必须验证：

- exact enumeration只在 `maximumExactCombinations` 内运行；
- 超过上限使用确定性、truthful `feasible_not_proven` 路径；
- `combinationsExplored`、objective、upper bound、absolute gap、exact/status 和 digest一致；
- 不把 heuristic 谎称为 optimal；
- malformed/overflow/invalid bound fail closed。

未来增加 wall-clock target 时必须使用独立 baseline与representative proposal distributions，不能复用 risk fixture。

## Required future executable budgets

### Gateway control

至少覆盖 admission、session/capability validation、tools list/describe、read-only call、mutation dispatch、queue full、slow handler、cross-owner fairness。记录 request bytes、concurrency、queue depth、timeout、p50–p999/max 和 rejection/drop。

### Snapshot

分别测量 single shard、coherent multi-shard vector、contention、gap/stale rejection 和 digest validation；记录 shard count、instrument count、reader/writer load、generation和lock wait。

### Portfolio compiler

覆盖 strategy/instrument cardinality、netting、budget overflow、lot/metadata mapping和fixed arithmetic。必须有代表性small/medium/limit fixtures。

### Execution authority

`journal-durable-to-send` 必须在目标 filesystem/durability mode 上测量，明确 append、fdatasync/fsync、queue、send handoff和emergency lane。Hosted tmpfs/ephemeral disk结果不能成为PAPER host SLA。

## Target-host qualification

任何低延迟或吞吐宣传必须在目标 host/profile上重复，并绑定：

```text
exact Git/binary/config digest
+ CPU/model/microcode/NUMA
+ kernel/governor/affinity/IRQ
+ compiler/linker/build flags
+ filesystem/mount/journal durability
+ venue/network mode
+ queue/load/fixture/version
+ full distribution and raw or histogram evidence
```

Host tuning 不能替代正确性、journal、risk、reconciliation 或 qualification。关闭安全检查、改变durability、隐藏drops、减少fault coverage或让safe-exit与普通队列竞争，均不是可接受优化。

## Acceptance and regression response

性能 gate 失败时：

1. 保留 exact failing distribution 和 environment；
2. 先确认 correctness/determinism 未变；
3. 对比相同 fixture/toolchain和原始分布；
4. 定位CPU、allocation、lock、I/O、queue和instrumentation变化；
5. 修复实现或提供独立审查的baseline change evidence；
6. 重新跑 exact head 与 merge candidate。

禁止重跑直到偶然成功、删除outlier、只报平均值、降低sample count、扩大threshold、把different host结果混合或用Simulator延迟替代PAPER证据。
