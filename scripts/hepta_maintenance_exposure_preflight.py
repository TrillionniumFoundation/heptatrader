"""Read-only fail-closed advisory for HeptaTrader PAPER maintenance.

This is NOT independent risk protection. It never grants stop/flatten authority.
Positive result is only a snapshot of observed FLAT state. Any maintenance
orchestration MUST additionally use atomic broker-owner fencing, and separately
validated protection; this tool cannot prevent a subsequent new entry.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import stat
import time

MAX_AGE_MS = 3_000
INSTRUMENTS = {"EUR.USD", "BTC.USD"}


def _amount(value):
    if type(value) is not str:
        return None
    try:
        n = Decimal(value)
        return n if n.is_finite() else None
    except InvalidOperation:
        return None


def classify(snapshot, now_ms):
    problems = []
    if not isinstance(snapshot, dict) or snapshot.get("schema") != "heptatrader.pnl.v1":
        return ["PNL_SCHEMA_UNKNOWN"]
    if snapshot.get("scope") != "STRATEGY_PERIOD_NOT_ACCOUNT_NAV":
        problems.append("PNL_SCOPE_INVALID")
    stamp = snapshot.get("observed_at_ms")
    expires = snapshot.get("valid_until_ms")
    if (type(stamp) is not int or type(expires) is not int or
            not stamp <= now_ms < expires or now_ms-stamp > MAX_AGE_MS):
        problems.append("PNL_STALE_UNKNOWN")
    if snapshot.get("complete") is not True:
        problems.append("PNL_INCOMPLETE")
    comps = snapshot.get("components")
    if not isinstance(comps, list) or len(comps) != len(INSTRUMENTS):
        return problems + ["INSTRUMENT_SET_UNKNOWN"]
    if {c.get("instrument") for c in comps if isinstance(c, dict)} != INSTRUMENTS:
        return problems + ["INSTRUMENT_SET_CHANGED"]
    for item in comps:
        name = item["instrument"]
        position = _amount(item.get("position"))
        broker_position = _amount(item.get("broker_position"))
        if position is None or broker_position is None:
            problems.append(name+":POSITION_UNKNOWN")
        elif position != broker_position:
            problems.append(name+":POSITION_MISMATCH")
        elif position != 0:
            problems.append(name+":EXPOSED")
        if item.get("ledger_complete") is not True:
            problems.append(name+":LEDGER_INCOMPLETE")
        if item.get("valuation_complete") is not True:
            problems.append(name+":VALUATION_UNAVAILABLE")
        pending = item.get("settlement_pending_command_ids_total")
        if type(pending) is not int or pending < 0:
            problems.append(name+":SETTLEMENT_UNKNOWN")
        elif pending != 0:
            problems.append(name+":SETTLEMENT_PENDING")
    return problems


def _read_private(path):
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or st.st_mode & 0o077 or st.st_size > 1024*1024):
            raise ValueError("SNAPSHOT_PRIVATE_SCOPE")
        return json.loads(os.read(fd, st.st_size))
    finally:
        os.close(fd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, default=Path.home()/".local/state/heptatrader/pnl/snapshot.json")
    args = ap.parse_args()
    try:
        problems = classify(_read_private(args.snapshot), int(time.time()*1000))
    except Exception:
        problems = ["SNAPSHOT_UNREADABLE"]
    # Even flat cannot be a stop permit: there is no atomic no-new-entry
    # lock / durable independent risk owner established here.
    print(json.dumps({"schema":"heptatrader.maintenance-exposure-advisory.v1",
                      "snapshot_findings":problems,
                      "advisory_stop_permitted":False,
                      "requires_independent_owner_continuity":True,
                      "may_place_orders":False,"may_stop_services":False},sort_keys=True))
    raise SystemExit(2 if problems else 3)


if __name__ == "__main__":
    main()
