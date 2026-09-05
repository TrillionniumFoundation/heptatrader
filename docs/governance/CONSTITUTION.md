# Hepta 系统宪章

Status: current normative
Applies to: repository-wide architecture, runtime, research and delivery
Verification: documentation, repository, contract, module and exact-revision gates
Authority: highest repository-wide normative authority

本宪章定义所有实现和未来扩展不可破坏的不变量。任何代码、注册表、PR 描述、运行手册或发布声明与本文件冲突时，以本文件为准；修改本文件属于 A3/O4 变更。

## 1. 交易权威

1. **Execution Authority 是唯一 venue mutation 权威。** Agent、策略、MCP、Gateway、全局优化器、研究和 Management Control Plane 均不得直接调用 Broker API。
2. 风险增加命令必须在外部发送前形成可验证的 durable journal 记录。
3. 幂等性绑定 stable command ID 与 canonical payload；同 ID 不同 payload 必须拒绝。
4. session、decision lease、execution epoch、fencing generation、state generation 和 watermark 在每个权威边界重新验证。
5. 订单、持仓、现金、PnL、风险使用和连接状态只来自 Execution/venue 权威投影。
6. 身份、配置、行情、汇率、状态、持久化、连接、对账或数值不确定时，风险增加 fail closed。
7. 可证明安全的 cancel、strict reduction 和 authoritative flatten 使用独立优先级通道，不得被普通策略拥塞阻断。
8. Unsupported venue 只能返回稳定、类型化失败，不得生成连接、ACK、订单号或成交的伪成功。
9. LIVE 默认不存在；激活 LIVE 需要独立威胁模型、O4 授权、实现、资格认证和显式能力变更。

## 2. 模块化与并发

1. 每个 active source、target、state owner、contract 和 deployment unit 必须映射到一个 ModuleManifest；暂时共享源只能标记 `shared-migration` 并绑定开放 gap。
2. 模块只通过版本化 contract 通信；禁止跨模块直接编译实现文件，禁止隐式共享可变全局状态。
3. 依赖图必须有向无环；实际 include/link/runtime dependency 不得超出 manifest。
4. 模块内部锁不得跨越跨模块调用、网络、Broker 或文件系统 I/O。
5. 每个 stateful 模块声明唯一 writer、generation、recovery、backpressure 和 overflow 语义。
6. 控制面 IPC 不承载行情、feature 或 proposal 的统一高频数据面。
7. 每个关键模块至少有 DRI、backup 和跨域 reviewer，关键知识 bus factor 不得为 1。

## 3. 全局决策

1. 策略模块输出 immutable、bounded `StrategyProposal`，不输出可直接发送的 Broker order。
2. Global Decision Plane 只基于完整、未过期、同一权威 snapshot vector 的 proposal set 生成 `AllocationPlan`。
3. “全局最优”只相对于声明的目标函数、约束、候选空间、snapshot、solver identity 和数值容差成立。
4. 非凸、超时或数值退化结果必须标记 `BEST_KNOWN`、`FEASIBLE_FALLBACK` 或失败，并报告 bound/gap；不得宣传为已证明最优。
5. AllocationPlan 不是交易命令，必须经过 canonical quantization、portfolio/risk、permit、journal 和 Execution revalidation。
6. optimizer、proposal 或 snapshot 不可用时不得增加风险；安全退出仍独立可用。

## 4. 研究、证据与供应链

1. 研究产物永不携带 session、credential、permit、PAPER/LIVE grant 或自动 promotion 权限。
2. capability、module、contract、gap 和 milestone 的完成状态不得由手工 prose 决定；same-revision evidence 具有最高状态权威。
3. 当前树只保留最新开发文档；旧文件名 alias、历史正文和手工 closure receipts 全部禁止。
4. 发布和 qualification 绑定 exact source、tree、artifact、config、toolchain、environment 和 evidence identity。
5. 法律与 vendor provenance 必须保留，但使用法律文件或机器可读 metadata，不形成第二套开发文档。
