# 验证策略

Status: current normative
Applies to: local development, pull requests, merge candidates, releases and qualifications
Verification: `docs/verification/test-matrix-v2.json`
Authority: verification authority

验证分四层：

1. **Lane A — Module fast**：文档/registry、changed module unit、contract compatibility、小型 deterministic fixture。
2. **Lane B — PR core**：双编译器 Debug/Release、全部 core contracts、Simulator E2E、install allowlist、受影响性能。
3. **Lane C — Merge candidate**：sanitizer、crash/replay、fuzz、长回放、完整 DAG、全局性能、可复现 package/SBOM。
4. **Lane D — External qualification**：受保护环境中的 exact source/artifact/config、真实 venue fault scenarios、soak、rollback。

一个 gap/capability/milestone 只有在实现、负向测试、规范文档和 exact-revision evidence 同时一致时才可派生为完成。

PR head 绿色不自动代表 merge candidate 绿色；手工 status、PR body 或旧 evidence 不构成完成；模块内部测试通过不代表系统 capability 已集成；external qualification 不可由 hosted mock 代替；correctness、authority 和 fail-closed 先于性能。

证据结构由 evidence-index schema 定义，不在 Git 中维护手工 `EXACT-HEAD-*` 状态文件。
