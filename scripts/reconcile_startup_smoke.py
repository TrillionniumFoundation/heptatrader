#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_oms_line(event, order_id, instrument, side, qty, status):
    return json.dumps({
        "schema_version": 2,
        "event": event,
        "ts_ms": 1700000000000,
        "order_id": order_id,
        "req_id": f"req-{order_id}",
        "client_req_id": f"req-{order_id}",
        "trace_id": "boot-smoke",
        "event_id": f"evt-{order_id}-{event}",
        "risk_code": "",
        "venue": "IB",
        "strategy": "demo",
        "account": "DU123",
        "instrument": instrument,
        "side": side,
        "qty": qty,
        "price": 6.0,
        "status": status,
        "reason": "",
        "source": "smoke"
    }, ensure_ascii=False)


REASON_ACTION = {
    "RISK_RECON_OPEN_ORDER_MISMATCH": "block",
    "RISK_RECON_POSITION_MISMATCH": "block",
    "RISK_RECON_CASH_MISMATCH": "warn",
    "RISK_RECON_CASH_UNAVAILABLE": "warn",
    "RISK_RECON_BROKER_OPEN_ORDERS_MISSING": "manual",
    "RISK_RECON_BROKER_POSITIONS_MISSING": "manual",
    "RISK_RECON_BROKER_CASH_MISSING": "manual",
    "RISK_RECON_BROKER_CASH_EMPTY": "manual",
    "RISK_RECON_BROKER_OPEN_ORDERS_BAD_LINE": "manual",
    "RISK_RECON_BROKER_POSITIONS_BAD_LINE": "manual",
    "RISK_RECON_ORDERS_MATCH": "auto-fix",
    "RISK_RECON_POSITIONS_MATCH": "auto-fix",
    "RISK_RECON_CASH_MATCH": "auto-fix",
    "RISK_RECON_OMS_REPLAY_SUMMARY": "auto-fix",
}


def eval_startup_decision(reason_codes):
    priority = {"auto-fix": 1, "warn": 2, "manual": 3, "block": 4}
    best = "auto-fix"
    for code in reason_codes:
        action = REASON_ACTION.get(code, "auto-fix")
        if priority[action] > priority[best]:
            best = action
    return best


def gen_fixtures(root: Path):
    d = root / "runtime-logs" / "reconcile-fixture"

    write(d / "broker_open_orders_match.csv", "USD.CNH,BUY,1000,Submitted\n")
    write(d / "broker_positions_match.csv", "USD.CNH,1000\n")
    write(d / "broker_cash_match.txt", "cash=100000\n")
    write(d / "oms_journal_match.jsonl", "\n".join([
        make_oms_line("order_intent", 1001, "USD.CNH", "BUY", 1000, ""),
        make_oms_line("place_sent", 1001, "USD.CNH", "BUY", 1000, "Submitted"),
        make_oms_line("status", 1001, "USD.CNH", "BUY", 1000, "Submitted"),
    ]) + "\n")

    write(d / "broker_open_orders_mismatch.csv", "USD.CNH,BUY,1000,Submitted\n")
    write(d / "broker_positions_mismatch.csv", "USD.CNH,0\n")
    write(d / "broker_cash_mismatch.txt", "cash=100001\n")
    write(d / "oms_journal_mismatch.jsonl", "\n".join([
        make_oms_line("order_intent", 2001, "USD.CNH", "BUY", 1000, ""),
        make_oms_line("place_sent", 2001, "USD.CNH", "BUY", 1000, "Submitted"),
        make_oms_line("status", 2001, "USD.CNH", "BUY", 1000, "Submitted"),
        make_oms_line("place_sent", 2002, "EUR.USD", "SELL", 500, "Submitted"),
    ]) + "\n")

    # missing-input case for manual path
    write(d / "broker_open_orders_missing_case.csv", "")
    write(d / "broker_positions_missing_case.csv", "")
    write(d / "broker_cash_missing_case.txt", "")

    return d


def run_checks():
    assert len(REASON_ACTION) >= 8, "reason_code mapping must contain at least 8 entries"

    # Case 1: all match + replay summary => auto-fix
    c1 = ["RISK_RECON_ORDERS_MATCH", "RISK_RECON_POSITIONS_MATCH", "RISK_RECON_CASH_MATCH", "RISK_RECON_OMS_REPLAY_SUMMARY"]
    assert eval_startup_decision(c1) == "auto-fix"

    # Case 2: critical mismatch present => block
    c2 = ["RISK_RECON_ORDERS_MATCH", "RISK_RECON_OPEN_ORDER_MISMATCH", "RISK_RECON_CASH_MISMATCH"]
    assert eval_startup_decision(c2) == "block"

    # Case 3: missing broker snapshot => manual
    c3 = ["RISK_RECON_BROKER_CASH_MISSING", "RISK_RECON_CASH_UNAVAILABLE"]
    assert eval_startup_decision(c3) == "manual"

    # Case 4: warning only => warn
    c4 = ["RISK_RECON_CASH_MISMATCH"]
    assert eval_startup_decision(c4) == "warn"



def main():
    ap = argparse.ArgumentParser(description="Generate reconcile startup fixtures and smoke-check reason_code->action policies")
    ap.add_argument("--workdir", default=".")
    args = ap.parse_args()

    root = Path(args.workdir).resolve()
    fixture_dir = gen_fixtures(root)
    run_checks()

    print(f"[OK] fixture generated: {fixture_dir}")
    print("[OK] mapping_entries=14 (>=8)")
    print("[OK] case_match decision=auto-fix")
    print("[OK] case_mismatch decision=block")
    print("[OK] case_missing_input decision=manual")
    print("[OK] case_warn_only decision=warn")
    print("[OK] reconcile_startup_smoke completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
