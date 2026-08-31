# Pull Request 工作流

Status: current normative
Applies to: all pull requests
Verification: change classification, module impact and exact-revision CI
Authority: pull-request process authority

每个 PR 描述必须包含 Change class、Affected modules、Affected contracts、Capability impact、Authority/state impact、Failure and rollback、Tests and performance、Migration/compatibility 和 Unsupported capability statement。

1. 从唯一 integration branch 创建短期分支。
2. 先改 registry/contract，再改 producer/consumer。
3. 运行 Lane A；提交前运行 `dev_core.sh`。
4. 请求 module owner 和必要 contract/safety reviewer。
5. Lane B 通过后进入 merge queue。
6. Lane C 在 exact merge candidate 上通过后才可合并。
7. 任何新失败会重新打开关联 gap；禁止手工修改 prose 宣告关闭。

复杂变更应拆成 contract、implementation、integration、qualification 四类可审查 PR。
