# XT/QMT venue plan

Status: unsupported event-normalization scaffold; outbound operations fail closed.

## Current state

`HeptaTrade/adapter_xt/` 保留未来 transport 所需的事件类型与 callback normalization API。公开构建没有 vendor SDK binding。`Init`、`Connect`、账户/持仓/行情请求、下单和撤单均不得返回真实成功；稳定原因码为 `XT_TRANSPORT_UNAVAILABLE`。即使测试注入 connected callback，也不能开启 outbound send。

## Required implementation sequence

1. 定义独立 transport process、IPC contract 和 vendor version/hash policy；
2. 解析配置但保持 `readOnly=true` 默认值；
3. 接入账户、持仓、订单、成交和连接 epoch 的 authoritative snapshot；
4. 把 callback 映射到 OMS v4，并实现 service-owned correlation；
5. 复用 Execution risk、journal-before-send、command ID、lease fencing 和 reconciliation；
6. 增加 mock contract、fault injection、restart recovery 和 Windows/Python ABI 测试；
7. 在受控 PAPER 环境先 read-only、后 bounded mutation；
8. 只有证据闭合后才能更新 capability matrix。

不允许使用本地 synthetic ack/status 代替真实 Broker callback，也不允许把 Simulator 行为命名为 `XT`。
