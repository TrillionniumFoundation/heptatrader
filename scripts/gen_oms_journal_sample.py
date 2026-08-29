#!/usr/bin/env python3
import json
import time


def now_ms():
    return int(time.time() * 1000)


def row(base, event, order_id, status="", reason="", risk_code=""):
    data = dict(base)
    data.update({
        "schema_version": 2,
        "event": event,
        "ts_ms": now_ms(),
        "order_id": order_id,
        "status": status,
        "reason": reason,
        "risk_code": risk_code,
    })
    data["event_id"] = f"{base['trace_id']}-{data['ts_ms']}-{event}-{order_id}"
    return data


def main(path="runtime-logs/oms_journal.sample.jsonl"):
    req = f"demo-{now_ms()}"
    order_id = 123456
    base = {
        "req_id": req,
        "client_req_id": req,
        "trace_id": f"boot-{now_ms()}",
        "venue": "IB",
        "strategy": "demo-strategy",
        "account": "DU_TEST",
        "instrument": "USD.CNH",
        "side": "BUY",
        "qty": 1000,
        "price": 6.0,
        "source": "demo",
    }

    rows = [
        row(base, "app_boot", -1, "ready"),
        row(base, "risk_check", -1, "passed"),
        row(base, "order_intent", -1),
        row(base, "place_sent", order_id, "submitted"),
        row(base, "status", order_id, "Submitted"),
        row(base, "cancel", order_id, "cancel_sent"),
        row(base, "status", order_id, "Cancelled"),
        row(base, "reject", -1, reason="price_deviation_too_large", risk_code="PRICE_DEV"),
    ]

    with open(path, "w", encoding="utf-8") as f:
        for row_data in rows:
            f.write(json.dumps(row_data, ensure_ascii=False) + "\n")

    print(path)


if __name__ == "__main__":
    main()
