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
- 测试必须实际执行，不接受手工创建的 success check 或 prose receipt。
- current capability 的 verification ID 必须存在且不能全部为 planned/external。
- external qualification 只能提升其绑定 artifact/environment，不能提升其他构建或 LIVE。
- correctness、authority、durability 和 fail-closed 先于 latency/throughput。
- flaky timing test 不能作为安全不变量证据；并发测试需要 barrier、virtual clock 或可观察状态。

证据遵循 `evidence-index-schema-v1.json`；Git 中不维护手工 `EXACT-HEAD-*` 文件。
