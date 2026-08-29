# OMS Event Schema (v2)

本文件定义 `runtime-logs/oms_journal.jsonl` 的事件模型（W3-W5 阶段B）。

## 目标

- 支持下单生命周期事件持久化（intent/place/status/cancel/reject/risk）。
- 支持重启后 replay 恢复订单状态。
- 支持基础幂等去重（event_id 优先，fallback 指纹）。
- 保持 CTP/IB 双接口兼容，不依赖 IB-only 字段。

## JSONL 记录格式

每行一个 JSON：

```json
{
  "schema_version": 2,
  "event": "status",
  "ts_ms": 1760000000000,
  "order_id": 101,
  "req_id": "req-xxx",
  "client_req_id": "req-xxx",
  "trace_id": "boot-1760000000000",
  "event_id": "boot-1760000000000-1760000001000-9",
  "risk_code": "",
  "venue": "IB",
  "strategy": "heptaStrategyDemo",
  "account": "DU1234567",
  "instrument": "USD.CNH",
  "side": "BUY",
  "qty": 1000.00000000,
  "price": 6.00000000,
  "status": "Submitted",
  "reason": "",
  "source": "ib.main_loop"
}
```

## 字段定义

- `schema_version`：当前为 `2`。旧日志（无该字段）默认按 v1 解析。
- `event`：事件类型（见下）。
- `ts_ms`：毫秒时间戳（epoch ms）。
- `order_id`：交易端订单号；未知时可为 `-1`。
- `req_id`：统一请求 id（推荐）。
- `client_req_id`：兼容旧字段，读写时与 `req_id` 对齐。
- `trace_id`：进程/会话追踪 id。
- `event_id`：事件幂等键（推荐全局唯一）。
- `risk_code`：风控拒绝/告警代码（如 `IB_PREFLIGHT`）。
- `venue`：交易通道（`CTP` / `IB` / 空）。
- `strategy`：策略名。
- `account`：账户标识。
- `instrument`：标的代码（如 `USD.CNH`）。
- `side`：`BUY` / `SELL`。
- `qty`/`price`：数量与价格。
- `status`：状态字段（如 `submitted`/`Cancelled`/`blocked`）。
- `reason`：失败/拒绝原因。
- `source`：事件来源模块（如 `ib.main_loop`）。

## 事件类型（推荐）

- `app_boot`：进程启动并完成 journal 恢复。
- `venue_connect`：通道连接结果。
- `risk_check`：风控检查通过。
- `risk_blocked`：风控阻断。
- `order_intent`：策略产生下单意图。
- `place_sent`：已发送下单请求。
- `status`：成交回报/状态更新。
- `cancel`：已发送撤单请求。
- `reject`：下单/撤单被拒绝。

## 回放恢复与幂等

`oms_recover` 模块在 replay 时：

1. 顺序读取 journal。
2. 先做去重：
   - 优先 `event_id`。
   - 若无 `event_id`，使用 `(event, ts_ms, order_id, req_id, status, reason, source)` 组合指纹。
3. 重建：
   - 每个 `order_id` 的最新状态、是否 `place_sent`、是否 `cancel_sent`、是否 `rejected`。
   - `req_id -> last_status` 索引。

## 最小回归

1) 生成样例日志：

```powershell
python scripts/gen_oms_journal_sample.py
```

2) 校验回放字段完整性：

```powershell
python scripts/verify_oms_journal_replay.py --journal runtime-logs/oms_journal.sample.jsonl
```

3)（可选）运行主程序并观察 `runtime-logs/oms_journal.jsonl` 中 `schema_version=2`、`trace_id`、`req_id`、`risk_code`。
