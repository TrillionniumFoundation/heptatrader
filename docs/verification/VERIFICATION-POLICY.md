# 验证策略

Status: current normative
Applies to: local development, pull requests, merge candidates, releases and qualifications
Verification: `docs/verification/test-matrix-v2.json`
Authority: verification authority

验证分四层：

1. **Lane A — Module fast**：generated docs、registry、changed-module unit、contract compatibility 和小型 deterministic fixture。
2. **Lane B — PR core**：每个 PR，包括 stacked base，执行 repository/module/schema checks、core contracts、Simulator E2E、安装 allowlist 与受影响性能。
3. **Lane C — Merge candidate**：精确候选执行 sanitizer、crash/replay、fuzz、长回放、完整 DAG、全局性能、可复现 package、SBOM 和供应链检查。
4. **Lane D — External qualification**：受保护环境绑定 exact source/tree/artifact/config/toolchain/harness/session，运行真实 venue、平台治理、fault、soak 和 rollback 验证。

## Evidence rules

- 历史绿色 run 不替代当前 SHA；PR head 绿色不自动代表 merge candidate 绿色。
- 测试必须实际执行，不接受手工创建的 success check、机械重跑或 prose receipt。
- required job 被 skip、cancel、timed out 或缺失时，结论不是 success。
- 同名 required context 只能由一个 workflow job 产生，禁止多个 workflow 复用 `core`、`test` 等模糊名称。
- current capability 的 verification ID 必须存在且不能全部为 planned/external。
- external qualification 只能提升其绑定 artifact/environment，不能提升其他构建或 LIVE。
- correctness、authority、durability 和 fail-closed 先于 latency/throughput。

## Pull request and merge queue checks

`.github/required-check-contexts-v1.json` 是 ruleset context 的机器权威。`required_branch_contexts` 同时投影为 PR 与 merge-group 列表，两个投影必须逐项、顺序和内容完全相同。

每个 required context 必须满足：

1. workflow job 有显式、稳定、全局唯一的 `name`；
2. 同一个 job 同时由 `pull_request` 与 `merge_group: checks_requested` 触发；
3. required job 本身没有导致其中一个事件被 skip 的 job-level 条件；
4. matrix context 只包含有界 matrix 值，不使用 ref、actor、SHA 等动态名称；
5. merge-group run 不会被 `cancel-in-progress` 取消；
6. ruleset 把 context 绑定到预期 GitHub Actions integration；
7. 当前 head 与 exact merge-group revision 上的最新同名 check 都是 terminal `success`。

`exact-merge-candidate` 在 PR 事件上验证 GitHub 合成的双亲 merge object 和 change impact，在 merge-group 事件上验证 merge queue exact revision；两种事件使用同一 required context，均执行 core 与 reliability，不产生 required-job skipped 状态。

## Platform governance evidence

静态检查分别验证：

- `check_workflow_check_contexts.py`：workflow context 唯一性、事件覆盖和取消策略；
- `check_required_context_projections.py`：PR/merge-group context 投影等价；
- `check_github_team_mapping.py`：全部 ModuleManifest owner 唯一映射到真实 team 目标，并保证 CODEOWNERS 模板无漂移；
- `verify_github_governance.py` 的离线 hostile-negative corpus：bypass、个人 owner、无权限 team、旧批准、失败重跑、缺失 merge-group check 等全部 fail closed。

这些静态证据不能关闭平台 gap。`G-TEAM-001` 只由受保护 `repository-governance` environment 中的 read-only live verifier 关闭。Verifier 必须从 GitHub API 读取 active ruleset、完整 bypass actors、default branch protection、CODEOWNERS errors、team identity/members/maintainers/permissions、PR author/reviews、source-head checks 和 merge-group checks；任何字段不可读取都视为证据不足。成功 receipt 绑定 repository、规则集、teams、PR、head SHA、merge-group SHA、required contexts 与每个 API response digest。

## Deterministic concurrency evidence

并发安全不变量不得由 sleep、线程启动概率、socket connect 数量或“多跑几次通常通过”证明。涉及队列、锁、epoch、lease、backpressure 或公平性的 fixture 必须建立可观察的状态机：

1. 用 callback/test seam 或 barrier 证明第一个请求已进入目标 active state；
2. 用受保护的 health/state witness 证明 bounded queue 已达到声明容量；
3. 在容量保持期间触发下一次 admission，并验证精确 rejection/result/reason code；
4. 在 blocker 尚未释放时验证不相关 shard/owner 仍能取得进展；
5. 先释放 barrier、join 全部线程、关闭 socket/worker，再执行最终断言。

连接建立、客户端线程启动或 elapsed time 只能作为诊断，不能替代队列状态证据。任何测试 interposer 必须观察生产对象公开或专用的有界状态，不能把尚未解码的连接误当作已排队请求。超时路径必须完成 teardown 并以失败结束，不能静默放行。

## Exact candidate closure

同一不变 revision 上必须完成其声明的 source-head jobs；进入合并队列后还必须对 exact merge-group revision 完成同一组 required checks。任何新提交都会使旧 review 与旧 check 失效。外部 PAPER 和 repository-governance qualification 是独立 Lane D，不由 core CI 推导。

证据遵循 `evidence-index-schema-v1.json`；Git 中不维护手工 `EXACT-HEAD-*` 文件。

## Internal closure floor

Every non-external verification check is implemented and backed by executable evidence on the exact candidate. Event ordering covers duplicate/idempotent, out-of-order, producer-epoch and sequence-gap behavior. Reconciliation covers divergence, outcome-uncertain and recovery convergence. Strategy isolation proves SHADOW and QUARANTINED modules cannot contribute to active allocation. Global allocation has a deterministic exact-combination ceiling and broad anti-hang deadline fixture in addition to same-toolchain regression gates.

`external` is reserved for protected, broker-observed or platform-observed qualification and cannot be converted to implemented by simulator, static policy or manually authored evidence.
