#!/usr/bin/env python3
import argparse
import json
from collections import defaultdict

REQUIRED = {"order_intent", "place_sent", "status", "cancel", "reject"}


def load_events(path):
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
                evt["_line"] = idx
                events.append(evt)
            except json.JSONDecodeError as e:
                print(f"[WARN] skip bad json line={idx}: {e}")
    return events


def replay(events):
    state = {}
    counts = defaultdict(int)
    for e in events:
        typ = e.get("event", "")
        counts[typ] += 1
        oid = e.get("order_id", -1)
        req_id = e.get("req_id") or e.get("client_req_id", "")
        if typ == "place_sent" and oid > 0:
            state.setdefault(oid, {})["placed"] = True
            state[oid]["req_id"] = req_id
        elif typ == "status" and oid > 0:
            state.setdefault(oid, {})["last_status"] = e.get("status", "")
        elif typ == "cancel" and oid > 0:
            state.setdefault(oid, {})["cancel_sent"] = True
        elif typ == "reject":
            key = oid if oid > 0 else f"reject@{e.get('_line')}"
            state.setdefault(key, {})["reject_reason"] = e.get("reason", "")
    return counts, state


def main():
    ap = argparse.ArgumentParser(description="Verify OMS journal append/replay skeleton")
    ap.add_argument("--journal", default="runtime-logs/oms_journal.jsonl")
    args = ap.parse_args()

    events = load_events(args.journal)
    if not events:
        print("[FAIL] no events found")
        return 2

    counts, state = replay(events)
    present = {k for k, v in counts.items() if v > 0}

    schema_v2_cnt = sum(1 for e in events if e.get("schema_version", 1) >= 2)
    trace_cnt = sum(1 for e in events if e.get("trace_id"))
    req_cnt = sum(1 for e in events if e.get("req_id") or e.get("client_req_id"))

    print(f"events_total={len(events)}")
    print(f"schema_v2_events={schema_v2_cnt}")
    print(f"trace_id_events={trace_cnt}")
    print(f"req_id_events={req_cnt}")
    print("event_counts=")
    for k in sorted(counts):
        print(f"  {k}: {counts[k]}")

    print(f"replayed_objects={len(state)}")
    sample_keys = list(state.keys())[:5]
    for k in sample_keys:
        print(f"  {k}: {state[k]}")

    missing = REQUIRED - present
    if missing:
        print(f"[WARN] required events missing: {sorted(missing)}")
        return 1

    if schema_v2_cnt == 0:
        print("[WARN] no schema_version>=2 events")
        return 1

    print("[OK] required events present; replay succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
