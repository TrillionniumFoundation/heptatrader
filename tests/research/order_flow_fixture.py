"""Synthetic data only; reusable by source tests and installed command smoke."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def order(seq, oid, actor, side, quantity, ticks, tif="GTC", instrument="A", time=None):
    return {"kind": "order", "seq": seq, "timestamp_us": seq if time is None else time,
            "instrument": instrument, "order_id": oid, "actor": actor, "side": side,
            "quantity": quantity, "limit_ticks": ticks, "time_in_force": tif}


def cancel(seq, oid, actor="RESEARCH", quantity=0, instrument="A", time=None):
    return {"kind": "cancel", "seq": seq, "timestamp_us": seq if time is None else time,
            "instrument": instrument, "order_id": oid, "actor": actor, "quantity": quantity}


def mark(seq, ticks, instrument="A", kind="mark", time=None):
    return {"kind": kind, "seq": seq, "timestamp_us": seq if time is None else time,
            "instrument": instrument, "price_ticks": ticks}


def events():
    return [order(1, "a-front", "EXTERNAL", "BUY", 4, 20),
            order(2, "a-own", "RESEARCH", "BUY", 3, 20),
            order(3, "a-seller", "EXTERNAL", "SELL", 5, 20, "IOC"),
            order(4, "a-seller-2", "EXTERNAL", "SELL", 2, 20, "IOC"),
            mark(5, 22),
            order(6, "a-fok", "RESEARCH", "SELL", 2, 22, "FOK"),
            order(7, "a-bid", "EXTERNAL", "BUY", 1, 22),
            order(8, "a-exit", "RESEARCH", "SELL", 2, 22, "FAK"),
            order(9, "b-ask", "EXTERNAL", "SELL", 4, 30, instrument="B"),
            order(10, "b-own", "RESEARCH", "BUY", 2, 30, instrument="B"),
            mark(11, 31, instrument="B"), mark(12, 22),
            mark(13, 22, kind="basis_rebase"),
            order(14, "a-day", "RESEARCH", "BUY", 1, 18, "DAY"),
            {"kind": "session_end", "seq": 15, "timestamp_us": 15, "instrument": "A"}]


def write_fixture(directory: Path, split: int | None = None):
    rows = events()
    chunks = [rows] if split is None else [rows[:split], rows[split:]]
    sources, entries = {}, []
    for index, chunk in enumerate(chunks):
        ref = "flow"+str(index)
        path = directory/(ref+".jsonl")
        data = ("\n".join(json.dumps(row, sort_keys=True) for row in chunk)+"\n").encode()
        path.write_bytes(data)
        sources[ref] = path
        entries.append({"ref": ref, "sha256": hashlib.sha256(data).hexdigest()})
    manifest = directory/"flow-manifest.json"
    manifest.write_text(json.dumps({"schema": "hepta.research.order-flow.v1", "capital": "1000",
        "currency": "USD", "max_mark_age_us": 100,
        "instruments": [{"instrument": "A", "tick_size": "0.5", "multiplier": "2", "lot": 1,
                         "fee_per_unit": "0.1", "fee_rate": "0"},
                        {"instrument": "B", "tick_size": "1", "multiplier": "3", "lot": 1,
                         "fee_per_unit": "0.2", "fee_rate": "0"}], "sources": entries}), encoding="utf-8")
    return manifest, sources
