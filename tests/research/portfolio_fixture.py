"""Synthetic portable portfolio inputs; no user data or broker fixtures."""
import hashlib
import json
from pathlib import Path

HEADER = "instrument,trading_day,begin_us,end_us,open,high,low,close,volume,ticks,complete"


def bar_bytes(instrument, prices, offset=0, incomplete=True):
    rows = [HEADER]
    for index, price in enumerate(prices):
        begin = offset + index*10
        complete = int(not incomplete or index+1 < len(prices))
        rows.append(f"{instrument},20260102,{begin},{begin+10},{price},{price},{price},{price},1,1,{complete}")
    return ("\n".join(rows)+"\n").encode("ascii")


def fixture(root: Path):
    paths, items = {}, []
    for name, prices, offset, quantity, multiplier, slip, fee in (
            ("A", [10,11,12,9,8,7], 0, "2", "3", "0.5", "0.25"),
            ("B", [20,19,18,21,22,23], 5, "1", "2", "0.25", "0.1")):
        data = bar_bytes(name, prices, offset)
        ref = name.lower()
        path = root/(ref+" bars.csv")
        path.write_bytes(data)
        paths[ref] = path
        items.append({"instrument": name, "currency": "USD", "tick_size": "1",
            "quantity": quantity, "multiplier": multiplier, "lot": "1", "slippage": slip,
            "fee_per_unit": fee, "fast": 1, "slow": 2, "long_only": False,
            "sources": [{"ref": ref, "sha256": hashlib.sha256(data).hexdigest()}]})
    document = {"schema": "hepta.research.portfolio-input.v1", "capital": "1000",
                "currency": "USD", "max_mark_age_us": 20, "instruments": items}
    manifest = root/"portfolio.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return manifest, paths, document


def check_report(report):
    assert report["schema"] == "hepta.research.portfolio-report.v1"
    assert report["mode"] == "OFFLINE_HYPOTHETICAL"
    assert report["positions"] == {"A": "-2", "B": "1"}
    from decimal import Decimal
    assert Decimal(report["final_equity"]) == Decimal("963.70")
    assert Decimal(report["fees"]) == Decimal("1.80")
    assert report["assumptions"]["broker_authorized"] is False
    assert report["assumptions"]["automatic_funding"] is False
    assert len(report["fills"]) == 4
    assert [f["timestamp_us"] for f in report["fills"]] == [20,25,40,45]
    assert report["equity"][-1]["timestamp_us"] == 55
