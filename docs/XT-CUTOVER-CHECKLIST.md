# XT 实盘切换最小清单（待批准后执行）

## 0. 前提
- QMT 交易端已登录且账号状态正常
- `HEPTA_VENUE=XT`
- 先保持 `HEPTA_ALLOW_XT_ORDERS=0`

## 1. 参数基线
```powershell
$env:HEPTA_VENUE='XT'
$env:HEPTA_XT_PATH='D:\国金证券QMT交易端\userdata'
$env:HEPTA_XT_ACCOUNT='<你的资金账号>'
$env:HEPTA_XT_ACCOUNT_TYPE='STOCK'
$env:HEPTA_XT_SESSION_ID='88888'
$env:HEPTA_XT_SYMBOL='000001.SZ'
$env:HEPTA_ALLOW_XT_ORDERS='0'
$env:HEPTA_GLOBAL_KILL_SWITCH='0'
$env:HEPTA_FLATTEN_ONLY='0'
$env:HEPTA_XT_MAX_ORDER_QTY='100'
$env:HEPTA_XT_MAX_DAILY_ORDERS='5'
$env:HEPTA_XT_MAX_PRICE_DEV_BPS='20'
```

## 2. 非交易联调（必须）
- 运行：`scripts/run_xt_scaffold_smoke.ps1`
- 验收：`OVERALL=PASS` 且 `oms_journal` 可见 XT 事件

## 3. 首笔小单验证（批准后）
1) 打开最小额度：
```powershell
$env:HEPTA_ALLOW_XT_ORDERS='1'
$env:HEPTA_XT_MAX_ORDER_QTY='100'
$env:HEPTA_XT_MAX_DAILY_ORDERS='1'
```
2) 仅允许白名单标的（建议先在 adapter/策略层限制到 1 个 symbol）
3) 下 1 笔最小测试单，观察：
- place_sent
- status
- cancel/reject/fill
- 对账/风控日志

## 4. 一键回滚开关
```powershell
$env:HEPTA_ALLOW_XT_ORDERS='0'
$env:HEPTA_GLOBAL_KILL_SWITCH='1'
$env:HEPTA_FLATTEN_ONLY='1'
```

## 5. 放量条件
连续 N 次（建议>=3）小单闭环成功后，再逐步提高：
- max qty
- max daily orders
- 可交易标的范围

## 6. 证据归档
每次联调保留：
- runtime-logs/xt-scaffold-smoke-*/summary.txt
- runtime-logs/oms_journal.jsonl 截取
- 账户/持仓快照
