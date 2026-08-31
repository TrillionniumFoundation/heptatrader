# Point-in-Time Data Contract

Status: current target contract
Applies to: datasets, market events, features, labels, replay and strategy validation
Verification: ordering, leakage, calendar, revision and digest tests
Authority: research/data temporal correctness

每条数据记录必须声明 authoritative event time、observation/ingest time、source/version、instrument identity 和 immutable digest。研究或 feature 只能读取决策时间可见的数据；修订数据以版本化 dataset snapshot 表达，不覆盖旧字节。

- 相同 timestamp 的冲突记录拒绝或按声明 revision policy 处理；
- timezone、session calendar、corporate action、symbol mapping 和 FX conversion 均版本化；
- label horizon、feature lookback、purge、embargo 和 final OOS 明确；
- missing、duplicate、out-of-order、stale 和 calendar gap 进入 data-quality result；
- dataset registry entry 绑定文件/对象 digest、许可、来源和可复现读取器。

无法证明 point-in-time 可见性的输入不能用于 PAPER promotion evidence。
