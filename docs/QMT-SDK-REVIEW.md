# QMT SDK 详细查阅结论（基于安装目录）

参考目录：`D:\国金证券QMT交易端\bin.x64\Lib\site-packages\xtquant`

## 1) 可确认的 SDK 形态

当前目录是 **Python SDK + 本地二进制扩展** 形态：
- 核心 Python 模块：`xttrader.py`, `xtconstant.py`, `xttype.py`, `xtdata.py`
- 二进制扩展：`xtpythonclient.cp3x-win_amd64.pyd`, `IPythonApiClient.cp3x-win_amd64.pyd`
- 依赖 DLL：`XtQuantServer*.dll`（安装目录其他层）、`log4cxx.dll` 等
- 文档：`doc/xttrader.pdf`, `doc/xtdata.pdf`

> 这说明“接口可用”是确定的；当前可直接绑定的是 Python SDK 语义层。

## 2) 交易接口（xttrader.py）关键入口

- `start()`
- `connect()`
- `run_forever()`
- `subscribe(account)`
- `order_stock(...)`
- `cancel_order_stock(...)`
- `query_stock_asset(...)`
- `query_stock_positions(...)`
- `query_stock_orders(...)`
- `query_stock_trades(...)`

## 3) 回调接口（XtQuantTraderCallback）

已确认存在：
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

这与 Hepta `adapter_xt` 的 callback bridge 设计可以一一对齐。

## 4) 常量与语义（xtconstant.py）

- 买卖方向：
  - `STOCK_BUY = 23`
  - `STOCK_SELL = 24`
- 价格类型：
  - `LATEST_PRICE = 5`
  - `FIX_PRICE = 11`
- 市场：
  - `SH_MARKET = 0`
  - `SZ_MARKET = 1`

## 5) 数据结构（xttype.py）

已确认对象：
- `StockAccount`
- `XtAsset`
- `XtOrder`
- `XtTrade`
- `XtPosition`
- `XtOrderError`
- `XtCancelError`
- 以及异步响应对象（如 `XtOrderResponse` 等）

字段可直接用于 Hepta OMS 事件标准化映射（账户/订单/成交/持仓/错误）。

## 6) 行情接口（xtdata.py）

已确认常用入口：
- `subscribe_quote(...)`
- `subscribe_whole_quote(...)`
- `get_market_data(...)`
- `get_full_tick(...)`
- `get_trading_dates(...)`
- `download_history_data(...)`

## 7) 对 Hepta 的实施建议（当前最优）

1. 先以 **现有 Python SDK 语义** 为规范层（已完成初步映射）。
2. `adapter_xt` 保持统一事件面（已具备 callback bridge 与 smoke）。
3. 后续若拿到 C++ SDK，则仅替换 transport 层，保留 OMS 事件语义不变。

## 8) 结论

你指定的目录确实是 QMT 可用 SDK 核心位置；
接口、回调、常量、数据结构都足够支撑 Hepta 的 XT 并列化接入工作。
