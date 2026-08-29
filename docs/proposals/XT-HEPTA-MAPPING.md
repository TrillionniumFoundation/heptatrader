# Hepta ↔ QMT(XT) 接口映射表（第一版）

Status: proposal
Applies to: proposed XT/QMT mapping; no active runtime capability
Verification: same-revision CI for repository placement only

参考路径：`D:\国金证券QMT交易端\bin.x64\Lib\site-packages\xtquant`
主要依据：`xttrader.py`、`xtconstant.py`

## 1) 会话与连接

| Hepta 统一接口 | XT(QMT) 对应接口 | 说明 |
|---|---|---|
| Connect() | `XtQuantTrader.start()` + `connect()` + `subscribe(account)` | QMT 要先 start，再 connect，再订阅账号 |
| Disconnect() | （建议：stop/进程退出） | `xttrader.py` 无显式 `disconnect` 封装，需在 adapter 内做生命周期管理 |
| PollOnce()/EventLoop | `run_forever()` + 回调 | 通过 callback 驱动事件上送 |

## 2) 下单/撤单

| Hepta 统一接口 | XT(QMT) 对应接口 | 关键参数映射 |
|---|---|---|
| PlaceOrder(symbol, side, qty, price, type) | `order_stock(...)` / `order_stock_async(...)` | `stock_code` ← symbol；`order_volume` ← qty；`price` ← price |
| CancelOrder(orderId) | `cancel_order_stock(account, order_id)` | Hepta orderId 对应 QMT order_id |
| CancelBySysId | `cancel_order_stock_sysid(account, market, sysid)` | 适合补偿场景 |

### 常用常量（xtconstant）
- 买卖方向：`STOCK_BUY=23`, `STOCK_SELL=24`
- 价格类型：`FIX_PRICE=11`, `LATEST_PRICE=5`
- 其他市价类型（上/深）可按交易所分支处理

## 3) 查询接口

| Hepta 查询接口 | XT(QMT) 对应接口 |
|---|---|
| QueryAsset() | `query_stock_asset(account)` |
| QueryPositions() | `query_stock_positions(account)` |
| QueryOrders() | `query_stock_orders(account, cancelable_only=False)` |
| QueryTrades() | `query_stock_trades(account)` |

## 4) 回调事件映射（关键）

`XtQuantTraderCallback` 中已看到的核心回调：
- `on_connected`
- `on_disconnected`
- `on_account_status`
- `on_stock_asset`
- `on_stock_order`
- `on_stock_trade`
- `on_stock_position`
- `on_order_error`
- `on_cancel_error`
- `on_order_stock_async_response`
- `on_cancel_order_stock_async_response`

### 建议映射到 Hepta OMS 事件
- `on_connected` -> `venue_connect(status=ok)`
- `on_disconnected` -> `venue_connect(status=down)`
- `order_stock*` 成功返回 -> `place_sent`
- `on_stock_order` -> `status`
- `on_stock_trade` -> `status/fill`
- `on_order_error` -> `reject(risk_code=XT_ORDER_ERROR)`
- `on_cancel_error` -> `reject(risk_code=XT_CANCEL_ERROR)`
- `cancel_order_stock*` -> `cancel` / `status=cancel_sent`

## 5) Adapter 实现建议（对齐 Hepta）
1. `adapter_xt` 内做三段式：`Init -> Connect -> SubscribeAccount`
2. 同步接口只做薄封装，主状态以回调为准
3. 强制落 OMS journal（与 IB/CTP 同 schema）
4. 统一风控前置：`globalKillSwitch/flattenOnly/maxQty/maxDaily`
5. 先做股票交易闭环，再扩展融资融券/两融等扩展接口

## 6) 当前边界
- 当前映射基于 Python `xtquant` API 语义。
- 若后续拿到 MiniQMT C++ SDK，建议保持相同事件语义层，替换 transport 层实现。
