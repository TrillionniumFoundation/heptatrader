# OMS event schema v4

`OmsJournal::kSchemaVersion` 当前为 `4`。journal 使用 JSONL，每行一个完整 JSON object；旧 v1-v3 记录可按兼容字段 replay，但新的 writer 必须写 v4。

## Core fields

```json
{
  "schema_version": 4,
  "event": "status",
  "ts_ms": 1760000000000,
  "order_id": 101,
  "req_id": "req-unique",
  "client_req_id": "req-unique",
  "trace_id": "service-epoch",
  "event_id": "globally-stable-event-id",
  "risk_code": "",
  "venue": "IB",
  "strategy": "strategy-id",
  "account": "paper-account",
  "execution_domain": "PAPER:domain",
  "request_hash": "sha256:...",
  "venue_correlation_id": "service-owned-correlation",
  "instrument": "EUR.USD",
  "side": "BUY",
  "qty": 1000.0,
  "price": 1.1,
  "status": "Submitted",
  "reason": "",
  "source": "execution"
}
```

`req_id` 与 legacy `client_req_id` 在新记录中保持一致。`event_id` 是事件去重键；mutation 的 retry 由 command ID/request hash 控制，不能仅依赖时间戳。

## Broker evidence fields

v4 可附加 `broker_callback_type`、`broker_service_epoch`、`broker_connection_epoch`、`broker_request_id`、`broker_error_code`、`broker_message`、`broker_advanced_order_reject_json`、`broker_why_held`、`broker_execution_id`、`broker_remaining_quantity` 和 `broker_market_cap_price`。这些字段用于保留完整 callback 证据，不能被 Agent 输入覆盖。

## Critical events

关键 mutation/恢复事件包括 `order_intent`、`place_send_attempt`、`place_sent`、`place_outcome_uncertain`、`cancel_send_attempt`、`cancel`、`flatten_intent`、`flatten_send_attempt`、`flatten_sent`、`flatten_noop`、`flatten_reject`、`flatten_outcome_uncertain`、`risk_blocked`、owner fencing、projection failure/resolution 和 Broker callback evidence。关键事件必须耐久写入后才能继续外部副作用。

## Replay rules

1. 只接受语法有效、schema version 合法且包含 event 的 object。
2. journal 必须为私有、单链接 regular file，且完整记录以换行结束。
3. replay 在整份输入验证通过前不调用消费者，避免部分回放。
4. `event_id` 重复、非法状态转换或 outcome uncertain 必须进入告警/对账，不能静默覆盖。
5. authoritative Broker snapshot 可以修正本地 projection，但修正本身必须留下事件证据。

最小验证：

```bash
python3 scripts/verify_oms_journal_replay.py --journal /path/to/oms-journal.jsonl
ctest --test-dir build --output-on-failure -R hepta_oms_journal_durability_tests
```
