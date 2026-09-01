# Point-in-Time Data Contract

Status: current target contract
Applies to: datasets, market events, features, labels, replay and strategy validation
Verification: ordering, leakage, coherent-cut, calendar, revision and digest tests
Authority: research/data temporal correctness

每条数据记录必须声明 authoritative event time、observation/ingest time、source/version、instrument identity 和 immutable digest。研究或 feature 只能读取决策时间可见的数据；修订数据以版本化 dataset snapshot 表达，不覆盖旧字节。

- 相同 timestamp 的冲突记录拒绝或按声明 revision policy 处理；
- timezone、session calendar、corporate action、symbol mapping 和 FX conversion 均版本化；
- label horizon、feature lookback、purge、embargo 和 final OOS 明确；
- missing、duplicate、out-of-order、stale 和 calendar gap 进入 data-quality result；
- dataset registry entry 绑定文件/对象 digest、许可、来源和可复现读取器。

运行时 `MarketDataSnapshot` 是不受信输入载体：feature 消费前必须重建 market event、验证 fixed-point/quote/time invariant，重算 event digest，并验证绑定 event digest、store generation 与 sequence-gap 的 snapshot-level digest。多 instrument 决策通过 canonical-order shard locking 读取一个 coherent participating-shard cut；逐 key 释放锁后再拼接的混合 cut 不得签发 authoritative vector digest。每个 vector 仍携带各组件 epoch、sequence、generation 与 fresh-until，调用方不能把 coherent read 误述为未实现的全系统 global generation。

ProposalSet 的 capture time 与 snapshot validity ceiling 必须来自上述 point-in-time cut；成员 proposal 的 expiry/horizon 与 snapshot ceiling 的交集成为后续 AllocationPlan 的不可延长上界。无法证明 point-in-time 可见性、snapshot digest 一致性或时间交集的输入不能用于 PAPER promotion evidence。
