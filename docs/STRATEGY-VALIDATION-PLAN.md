# Strategy validation plan

策略验证必须使用可移植参数，不在文档或代码中固定开发者磁盘路径。

## Phase A: deterministic replay

通过环境变量或命令行指定版本化 replay dataset、index 和只读 simulator config。记录 dataset hash、config hash、commit、compiler、seed、交易日历和输出目录。同一输入重复运行时，订单事件应逐条一致；浮点汇总指标只在事先定义的容差内比较。

## Phase B: semantic equivalence

比较基线、重构和参数实验：总收益、最大回撤、风险暴露、成交数、滑点、拒单、turnover 与每笔订单因果链。任何差异必须能追溯到明确代码、参数或数据变化，不能只比较最终 PnL。

## Phase C: shadow

策略只生成决策 receipt 和预期 TradeIntent，不获得 execution capability。验证数据新鲜度、时区、交易时段、重复输入、重启状态和 missing-data fail-closed。

## Phase D: bounded Simulator

通过 Agent OS 的真实 Gateway/Execution/OMS 路径运行，而不是直接调用 legacy strategy-to-broker API。执行断线、延迟、重复 callback、部分成交、cancel race、journal failure 和 reconciliation 故障注入。

## Phase E: controlled PAPER

仅针对 capability matrix 中的 Conditional venue，在受控 runner 上先 read-only，再经人工批准运行 bounded mutations。证据必须绑定 commit、SDK、账户、限制、时间范围和完整 journal。任何 P1 或无法解释偏差都回到前一阶段。
