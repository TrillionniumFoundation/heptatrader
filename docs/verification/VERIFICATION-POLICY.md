# 验证策略

Status: current normative
Applies to: local development, pull requests, merge candidates, releases and qualifications
Verification: `docs/verification/test-matrix-v2.json`
Authority: verification authority

验证分四层：

1. **Lane A — Module fast**：generated docs、registry、changed-module unit、contract compatibility 和小型 deterministic fixture。
2. **Lane B — PR core**：每个 PR，包括 stacked base，执行 repository/module/schema checks、core contracts、Simulator E2E、安装 allowlist 与受影响性能。
3. **Lane C — Merge candidate**：精确候选执行 sanitizer、crash/replay、fuzz、长回放、完整 DAG、全局性能、可复现 package、SBOM 和供应链检查。
4. **Lane D — External qualification**：受保护环境绑定 exact source/tree/artifact/config/toolchain/harness/session，运行真实 venue fault、soak 和 rollback。

## Evidence rules

- 历史绿色 run 不替代当前 SHA；PR head 绿色不自动代表 merge candidate 绿色。
- 测试必须实际执行，不接受手工创建的 success check、机械重跑或 prose receipt。
- required job 被 skip、cancel、timed out 或缺失时，结论不是 success。
- current capability 的 verification ID 必须存在且不能全部为 planned/external。
- external qualification 只能提升其绑定 artifact/environment，不能提升其他构建或 LIVE。
- correctness、authority、durability 和 fail-closed 先于 latency/throughput。

## Deterministic concurrency evidence

并发安全不变量不得由 sleep、线程启动概率、socket connect 数量或“多跑几次通常通过”证明。涉及队列、锁、epoch、lease、backpressure 或公平性的 fixture 必须建立可观察的状态机：

1. 用 callback/test seam 或 barrier 证明第一个请求已进入目标 active state；
2. 用受保护的 health/state witness 证明 bounded queue 已达到声明容量；
3. 在容量保持期间触发下一次 admission，并验证精确 rejection/result/reason code；
4. 在 blocker 尚未释放时验证不相关 shard/owner 仍能取得进展；
5. 先释放 barrier、join 全部线程、关闭 socket/worker，再执行最终断言。

连接建立、客户端线程启动或 elapsed time 只能作为诊断，不能替代队列状态证据。任何测试 interposer 必须观察生产对象公开或专用的有界状态，不能把尚未解码的连接误当作已排队请求。超时路径必须完成 teardown 并以失败结束，不能静默放行。

## Exact candidate closure

同一不变 revision 上必须完成其声明的 source-head jobs；进入合并队列后还必须对 exact merge candidate 完成 Lane C。任何新提交都会使旧 review 与旧 check 失效。外部 PAPER qualification 是独立 Lane D，不由 core CI 推导。

证据遵循 `evidence-index-schema-v1.json`；Git 中不维护手工 `EXACT-HEAD-*` 文件。
