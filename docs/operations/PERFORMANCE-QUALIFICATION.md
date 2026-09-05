# Performance Qualification

Status: current normative
Applies to: runtime latency, throughput, queue and host-tuning claims
Verification: performance budget registry, same-fixture executable gates and target-host observations
Authority: performance-claim policy

性能声明必须绑定 source、binary、compiler/toolchain、build type、fixture、hardware/VM、kernel、CPU governor、affinity、queue load、sample distribution 和 correctness result。只报告平均值不足以支持交易运行时声明，至少记录 p50/p95/p99/p999、max、sample count、drop/backpressure 和 CPU/memory。

## Budget states and evidence scope

`performance-budgets-v1.json` 每个 entry 明确为：

- `declared`：目标和允许回退比例已经定义，但缺少 canonical absolute baseline、representative fixture 或 target-host distribution；**不能支持性能、readiness、release 或 qualification 声明**；
- `implemented`：存在同一 revision 的 executable fixture、机器绑定 threshold/complexity contract 和可记录 source/build/toolchain identity。

`implemented` 还必须解释 scope：

- `repository-ci`：只证明同 fixture、Release build、托管 runner/toolchain 下的回归上界；不能支持部署、PAPER、网络或产品 SLO；
- `bounded-complexity`：证明算法探索上限、deadline/fallback 和 truthful bound，不自动形成 wall-clock SLO；
- target-host 性能只有受保护环境在精确 artifact/config/host 上生成完整分布后才能成立。

当前 repository-ci 已实现：

- `risk-policy-v1`：固定点风险评估；
- `gateway-control-v1`：进程内 capability/environment/schema 校验、read callback 和 bounded JSON 结果校验；
- `snapshot-v1`：七次权威子读取、复合决策快照校验、digest 与 generation 发布；
- `portfolio-compiler-v1`：64 strategy × 16 instrument 的固定点净额、预算和 delta 编译。

`global-allocator-v1` 是 implemented bounded-complexity contract。`execution-authority-v1` 仍为 declared target-host budget；仓库 CI 的 critical OMS append+`fdatasync` fixture 只是回归烟测，不能成为 PAPER host SLA。

## Canonical repository fixtures

每个 wall-clock fixture 的唯一 threshold 来自 `benchmarks/*.json`。CMake 使用 `string(JSON ...)` 读取 exact fixture、p99 和 regression percentage，并把数值编译进对应测试；C++ 不保留第二套手写阈值。Baseline 记录 operation scope、Release build、runner class、warmup、sample count 和 claim ceiling；fixture 输出 compiler family/version、`__cplusplus`、完整 percentile distribution 和 allowed threshold。Workflow/check record 绑定 exact source SHA 与 runner image。

### Risk

`hepta_risk_latency_fixture_tests` 预热 500 次并采集 10,000 次 exact fixed-point evaluation。其 hosted 数值不是生产承诺。

### Gateway validated read dispatch

`hepta_gateway_latency_fixture_tests` 对 `system.get_health` 运行 1,000 次预热和 10,000 次采样，覆盖 registry lookup、capability、environment、typed-call validation、read callback、bounded JSON validation 和 result construction。它不覆盖 AF_UNIX、排队、慢 handler、跨 owner 公平性或下游 Execution RPC；这些必须作为更高层 fixture 单独资格化。

### Authoritative decision snapshot

`hepta_snapshot_latency_fixture_tests` 预热 100 次并采集 2,000 次完整复合快照，验证 before/after health identity、quote currentness、account/positions/orders/risk authoritative flags、owner identity、canonical JSON、digest、watermark 和 generation。它不替代 Market Data 多 shard contention 或目标主机负载测试。

### Portfolio compiler

`hepta_portfolio_latency_fixture_tests` 对 64 strategy × 16 instrument、1,024 intents 的 limit profile 预热 100 次并采集 2,000 次。Correctness tests仍单独覆盖 overflow、duplicate、generation、budget 和 canonical ordering；性能 fixture 不允许通过减少 cardinality 来规避门禁。

### OMS durable append repository smoke

`hepta_oms_journal_latency_fixture_tests` 强制同步 critical event，在每次样本中执行 path identity、write 和 `fdatasync`，并验证 durable-write counters 与 poison/failure 状态。托管 runner 的 `/tmp` filesystem、虚拟化与噪声不是 PAPER 主机，因此该 fixture 只能发现显著仓库回归，不能关闭 `execution-authority-v1` 的 target-host evidence。

## Global allocator budget

Global allocator 的 implemented budget 当前是 bounded complexity/deadline contract，而非通用 wall-clock SLA。Evidence 必须验证：

- exact enumeration只在 `maximumExactCombinations` 内运行；
- 超过上限使用确定性、truthful `feasible_not_proven` 路径；
- `combinationsExplored`、objective、upper bound、absolute gap、exact/status 和 digest一致；
- 不把 heuristic 谎称为 optimal；
- malformed/overflow/invalid bound fail closed。

未来增加 wall-clock target 时必须使用独立 baseline与representative proposal distributions，不能复用 risk fixture。

## Remaining higher-layer budgets

### Gateway end to end

仍需在目标拓扑覆盖 AF_UNIX admission、session lease lookup、tools list/describe、mutation RPC、queue full、slow handler、cross-owner fairness和response encoding；记录 request bytes、concurrency、queue depth、timeout、p50–p999/max 和 rejection/drop。

### Market Data and snapshot contention

仍需分别测量 single shard、coherent multi-shard vector、contention、gap/stale rejection 和 digest validation；记录 shard count、instrument count、reader/writer load、generation和lock wait。

### Execution authority target host

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
