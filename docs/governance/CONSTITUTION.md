# Hepta 系统宪章

Status: current normative
Applies to: repository-wide architecture, runtime and delivery
Verification: `python3 scripts/check_documentation_control_plane.py` and applicable same-revision gates
Authority: highest repository-wide normative authority

本文件定义所有模块、控制面、数据面、研究工具、部署工具和未来扩展都不得破坏的最高级不变量。任何实现、计划、PR 描述或运行手册与本文件冲突时，以本文件为准；改变本文件属于 A3/O4 级变更。

## 1. 权威与安全不变量

1. **Execution Authority 是唯一 venue mutation 权威。** Agent、策略模块、MCP、Gateway、全局优化器和管理控制面都不得直接调用 Broker API。
2. **新风险命令必须 journal-before-send。** 未完成持久化不得产生外部副作用。
3. **幂等性绑定 command ID 与规范化 payload。** 相同 ID、相同 payload 返回持久结果；相同 ID、不同 payload 必须冲突拒绝。
4. **epoch、fencing、generation 和 watermark 必须在权威边界重新验证。**
5. **账户、订单、持仓、现金、PnL、风险使用量和连接状态来自 Execution/venue 的权威投影。**
6. **未知状态对风险增加 fail closed。** 包括身份、配置、行情、汇率、快照、持久化、连接、对账和数值异常。
7. **安全退出优先。** 在可以证明安全时，cancel、strict reduce-only 与 authoritative flatten 不得被普通策略拥塞或新风险门禁阻断。
8. **不支持的 venue 不得伪造成功。** 必须返回稳定、类型化的 unsupported 结果。
9. **研究产物不携带运行时能力。** 回测、SHADOW、报告或模型文件不能授予 session、permit、credential、PAPER 或 LIVE 权限。
10. **LIVE 默认不存在。** 任何 LIVE 能力必须通过独立威胁模型、授权、资格认证和显式能力升级。

## 2. 模块化不变量

1. 每个 active source、build target、state owner、contract 和 deployment unit 必须归属于唯一 ModuleManifest。
2. 模块只能通过版本化 contract 通信，不得跨模块直接包含或编译实现文件。
3. 实际依赖必须是已声明的有向无环图；禁止逆向越层依赖和隐式共享全局状态。
4. 模块内部锁不得跨越模块调用、Broker I/O、文件系统 I/O 或网络 I/O。
5. 每个模块必须声明 failure、timeout、backpressure、resource budget、determinism 和 rollback 语义。
6. 每个关键模块至少有 DRI team、backup team 和跨域 contract reviewer。

## 3. 全局决策不变量

1. 策略模块输出 `StrategyProposal`，不输出可直接发送的 Broker order。
2. Global Decision Plane 只能基于完整、版本化、未过期的 proposal 集和 authoritative snapshot 生成 `AllocationPlan`。
3. “全局最优”只相对于明确的目标函数、约束、候选空间、snapshot 和数值容差成立。
4. 非凸或超时求解不得被描述为已证明全局最优；必须报告 `BEST_KNOWN`、bound 或 `optimality_gap`。
5. AllocationPlan 必须再次经过 deterministic risk、permit、journal 和 Execution Authority。
6. 优化器不可用、输入过期或数值失败时，不得增加风险；安全退出保持独立可用。

## 4. 文档与证据不变量

1. 规范性事实只在一个权威文档或注册表中定义。
2. capability、module、contract、milestone 和 gap 状态由机器注册表及 same-revision evidence 派生。
3. 手工 prose、PR 描述或历史绿色检查不能覆盖当前提交的失败或缺失证据。
4. 历史文档不得保留在 active documentation graph；兼容路径只能是无独立正文的 alias。
5. 所有发布和资格认证必须绑定 exact source SHA、artifact digest、配置身份和证据身份。
