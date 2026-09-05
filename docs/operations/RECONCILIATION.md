# Reconciliation and Uncertain Outcomes

Status: current normative
Applies to: OMS, state authority, Execution and qualified venue adapters
Verification: crash/replay, disconnect, duplicate/out-of-order, divergence and protected qualification scenarios
Authority: authoritative recovery

Reconciliation 比较两个独立事实域：

```text
durable local truth                       venue/Broker observed truth
command ID + normalized payload digest    client/order/perm/execution identities
journal sequence + status                 open orders + executions + terminal states
execution epoch/fence                     connection/session/next-valid-ID state
position/account projection               authoritative positions/cash/account values
```

策略、Agent、Gateway cache、客户端 timeout、模型文本和 telemetry 都不能代替任一侧的 authority。

## Trigger conditions

以下任一事件必须运行 reconcile，并在完成前关闭风险增加：

- process startup/restart；
- broker/venue disconnect/reconnect；
- send 返回 uncertain 或 transport acknowledgement 丢失；
- duplicate/out-of-order/correction callback；
- journal replay、sequence、digest、inode或write fault；
- open-order/position/account divergence；
- execution epoch/fence 或 session owner 变化；
- periodic shadow reconcile；
- rollback、config/account/SDK change；
- kill switch disarm 前；
- PAPER qualification scenario。

## Preconditions

1. new-risk gate 已关闭；
2. exact binary、config source/canonical digest、journal identity、execution epoch/fence 已记录；
3. Broker/venue connection 处于可查询状态，或明确记录 unavailable；
4. 不生成新 command ID、不改变原 payload、不清理 local state；
5. 收集窗口有明确 observed-at、complete/end marker 或 timeout；
6. callback consumer 保持 current owner，旧 epoch 被 fence。

只读 Gateway 可用时，可获取诊断快照：

```bash
export HEPTA_TOOL_SOCKET=/run/hepta-agent/tools.sock
export HEPTA_TOOL_SESSION_TOKEN='<controlled-injection>'
heptactl call system.get_health
heptactl call orders.list
heptactl call portfolio.list_positions
heptactl call account.get_summary
```

不要保存 token。Gateway snapshot 仅作为受签发 session/currentness保护的查询结果；真正的 venue reconciliation 仍由 Execution/adapter 完成。

## Canonical algorithm

### 1. Pin local durable state

- 打开 canonical journal path，验证 regular/single-link、owner/mode、schema/version；
- 从最后已验证 checkpoint 或文件起点按 sequence replay；
- 验证 command ID、payload digest、epoch/fence、status transition 和 venue correlation；
- 相同 command ID + 相同 payload 合并为同一 durable identity；
- 相同 command ID + 不同 payload 是冲突/P1，不能选择其中一个；
- truncated/corrupt/unknown record 不跳过，journal 标记 poisoned并保持 gate closed。

### 2. Collect one venue observation window

Adapter 必须收集：

- connection/session/client identity；
- open orders 与 terminal order states；
- executions/fills/commissions（按 contract需要）；
- positions；
- cash/account/risk inputs；
- next-valid order identity或等价 venue sequencing；
- observation start/end/currentness；
- duplicate/correction identity。

缺失 complete marker、callback backlog未排空或 source stale 都视为不完整，不能把未出现对象当成不存在。

### 3. Normalize identities

所有比较使用 canonical contract fields、fixed-point numeric policy 和明确 venue mapping。Price/quantity/cash scale、currency、instrument、account/environment、order/client/perm/execution IDs、status 枚举和 timestamps 先验证再 admission。未知、NaN/Inf、scale mismatch、负零、overflow或模糊 mapping 不能归零。

### 4. Classify every object

每个 local command / venue order / execution / position 至少归入：

| Class | Meaning | Required action |
|---|---|---|
| `matched` | identity/payload/lifecycle一致 | retain canonical state |
| `local-pending` | durable command已存在，venue仍未证明 | keep uncertain/query |
| `venue-only` | venue对象没有local durable authority | isolate/P1/manual decision |
| `local-only-terminal` | local terminal且venue complete window证明不存在/终结 | retain terminal evidence |
| `duplicate` | same identity/same semantics | idempotent merge |
| `conflict` | same identity/different payload/state | terminal latch/P1 |
| `stale-epoch` | output来自已fence owner | reject，不修改current state |
| `correction` | venue明确修正先前事件 | apply versioned lifecycle rule |
| `unresolved` | observation不完整或无法证明 | keep new-risk closed |

### 5. Resolve uncertain commands

原 command ID 和 normalized payload digest 永远不变：

1. query local durable outcome；
2. query exact venue correlation/open order/execution；
3. merge duplicate/out-of-order callbacks using event identity/lifecycle validator；
4. venue证明 accepted/filled/cancelled/rejected 后写入对应 durable transition；
5. venue明确证明 request 未被接受且 contract 允许 retry 时，仍由 authoritative coordinator决定是否复用同一 command identity；
6. 任何无法证明的情况保持 uncertain，不创建“replacement order”。

Transport timeout、socket close、TWS UI显示、operator推测或“没收到 callback”都不能证明未成交。

### 6. Reconcile projections

- order state 从 durable lifecycle + venue observations生成；
- position 从 executions/authoritative position snapshot收敛；
- account/cash/PnL字段按启用risk规则决定是否 required；
- snapshot generation只在完整 cut成功后前进；
- 任一 required component conflict/stale/missing时整个 risk-ready snapshot拒绝；
- reconciliation写入 stable result/reason code，不覆盖原始 evidence。

## Outcome states

动作优先级不可被 operator降级：

```text
terminal-latched / block
    > isolated-manual-remediation
    > warn-but-contained
    > resolved
```

`resolved` 要求：

- local replay完整且journal not poisoned；
- venue observation window完整/current；
- 所有 command/order/execution identity分类完成；
- 没有 venue-only/conflict/stale-writer 泄漏；
- positions、required account/risk inputs一致；
- uncertain command全部终结或被明确隔离且不影响新风险；
- current execution epoch/fence与snapshot/permit context一致；
- result 已 durable记录并可 replay。

`warn` 不能用于绕过启用的 risk input。Open-order/position mismatch、unknown send outcome、epoch/fence disagreement、journal poison一律至少 block。

## Recovery and gate reopening

Reconcile 成功后仍按 [Startup and Readiness](STARTUP.md) 执行：

1. publish new authoritative snapshot generation；
2. invalidate旧 proposal/plan/permit/session cache；
3. re-evaluate risk和quote freshness；
4. 验证 kill switch/terminal latch；
5. 先开放只读流量，再按策略开放新风险；
6. 记录 `hepta_reconcile_runs_total` outcome/reason和相关 state-break evidence。

Systemd `active`、Broker reconnect 或一次 matched order 都不能单独打开 gate。

## Failure handling

无法证明收敛时：

- 保持 terminal latch/new-risk closed；
- 保留 cancel/strict reduce/flatten 的独立 authority验证；
- 隔离 wrong-account/venue-only object；
- 升级 incident，不删除或修改 journal；
- 必要时回滚完整 artifact/config，但回滚后仍重新 replay/reconcile；
- 禁止直接 broker API 下单、伪造 callback、手工改 snapshot/status、改 command ID 或把 unknown当零。

## Evidence

每次 reconcile 保存：trigger、exact source/binary/config、journal path/inode/size/digest/schema、execution epoch/fence、venue/session fingerprint、observation window、对象分类计数、所有 conflict/unresolved reason、before/after snapshot generation、operator/automation identity和最终 outcome。Raw credential、token、完整 account ID和无界 payload不得进入 evidence。
