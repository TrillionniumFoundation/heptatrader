# IB 多策略状态持久化（最小版本）

## 开关
- `HEPTA_STRATEGY_STATE_PERSIST=1`：启用策略状态持久化。
- `HEPTA_STRATEGY_STATE_PATH`（可选）：自定义状态文件路径；默认 `runtime-logs/strategy_state.json`。

## 持久化字段（每个策略）
- `lastSignalTs`
- `lastTradeTs`
- `cooldownUntil`
- `positionIntent`（`LONG`/`SHORT`/`FLAT`）
- `entryTs`
- `netPosition`
- `avgEntryPrice`

## 行为
1. 启动并完成 `ibStrategyEngine.Configure(...)` 后，如果开关开启，自动从状态文件加载恢复。
2. 运行中（IB 多策略且非测试单循环）每约 3 秒落盘一次。
3. 写入采用原子方式：先写 `*.tmp`，再原子替换目标文件（Windows 使用 `MoveFileEx(..., MOVEFILE_REPLACE_EXISTING)`）。

## 验证步骤
1. 启动前设置：
   - `set HEPTA_STRATEGY_STATE_PERSIST=1`
   - （可选）`set HEPTA_STRATEGY_STATE_PATH=runtime-logs/strategy_state.json`
2. 启动程序并让策略运行一段时间。
3. 检查 `runtime-logs/strategy_state.json` 已生成，且包含上述字段。
4. 结束进程后再次启动，观察日志出现：`IB strategy state loaded: ...`。
5. 在冷却期内重启，确认策略不会立即重复下单（`cooldownUntil` 生效）。
