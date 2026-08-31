# 调试与故障定位

Status: current
Applies to: developers and operators
Verification: structured logs, reason codes, replay and fault fixtures
Authority: debugging guidance

1. 确认 exact source SHA、binary/config/schema digest。
2. 找到 stable reason code，而不是先阅读自由文本。
3. 确认 execution epoch、fence、state generation、watermarks。
4. 查询 command ID 的 journal 和 durable outcome。
5. 对比 Broker/venue authoritative orders、positions 和 executions。
6. 使用同一输入运行 deterministic replay。
7. 只在状态真相建立后分析性能或策略逻辑。

禁止通过删除 journal、改变 command ID、手改 snapshot、关闭 risk 或绕过 Gateway/Execution 来“修复”测试。
