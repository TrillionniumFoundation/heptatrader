# QMT -> HeptaTrader Bridge (MVP)

目标：把 QMT 侧导出的篮子订单 CSV 转成 Hepta OMS v2 JSONL 事件，先打通“信号入湖”，再接执行器。

## 1) 生成 QMT 篮子文件

在 QMT Python 项目中生成：

- 文件示例：`QMT_Order_Basket_YYYYMMDD.csv`
- 关键列：`证券代码,交易方向,委托数量,委托价格(可选)`

## 2) 转换为 Hepta OMS 事件

脚本：`C:/Users/Administrator/Downloads/quant/quant/scripts/bridge_qmt_to_hepta.py`

示例命令：

```powershell
D:/anaconda/python.exe C:/Users/Administrator/Downloads/quant/quant/scripts/bridge_qmt_to_hepta.py `
  --input C:/Users/Administrator/Downloads/quant/quant/QMT_Order_Basket_20260228.csv `
  --output D:/quant/HeptaTrader-master/runtime-logs/qmt_bridge_orders.jsonl `
  --venue CTP --account SIM
```

## 3) 输出事件结构

每条订单写两条事件（MVP）：

- `order_intent`
- `place_sent` (status=`queued_by_qmt_bridge`)

字段遵循 `docs/OMS-EVENT-SCHEMA.md`（schema_version=2）。

## 4) 当前边界

- 仅完成“QMT 信号 -> Hepta OMS 日志”桥接。
- **尚未自动落地下单**（需要在 Hepta 侧增加 qmt_bridge 执行消费器）。
- 适合作为联调和审计入口，不建议直接用于生产交易。

## 5) 下一步（建议）

1. 在 HeptaTrade 增加 `qmt_bridge_consumer`：监听 `qmt_bridge_orders.jsonl`，调用现有 OMS 下单路径。
2. 回写 `status/reject/cancel` 到 `oms_journal.jsonl`，并做 req_id 对账。
3. 增加最小回归：重复导入去重、异常行隔离、断点续跑。
