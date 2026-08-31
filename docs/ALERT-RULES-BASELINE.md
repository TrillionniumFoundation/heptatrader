# Alert rules baseline

## P1

- `OMS_JOURNAL_MALFORMED`：journal 含非法或超大记录。停止新增风险，保留文件并验证 durability/replay。
- `OMS_DUPLICATE_EVENT_ID`：事件 ID 重复。检查 producer、重启恢复和幂等边界。
- `OMS_OUTCOME_UNCERTAIN`：存在 place/flatten/cancel/projection 不确定结果。先 authoritative reconciliation，禁止盲目重发。
- `IB_ORDER_REJECTED_201`：IB 错误 201。暂停新增订单并核查账户权限、合约、价格、数量和风控。
- `OMS_NO_EVENTS`：在显式 `--require-events` 模式下没有有效事件。
- collector 执行失败：文件 identity、owner、mode、I/O 或输出路径不安全。

## P2

建议部署侧基于 metrics 增加：事件长时间停滞、active orders 超过预期、某 risk code 激增、Broker reconnect 频率异常和 journal sync latency 上升。阈值必须按运行模式与交易时段配置，不能对离线 Simulator 使用实时交易阈值。

## Response

P1 首先 engage kill switch 或保持只读；保存 journal、alerts、metrics、service logs、build provenance 和当前配置 hash；再按 `RUNBOOK-INCIDENT.md` 分类处理。只有 authoritative state 已确认、根因闭合、回归通过后才恢复新增风险。
