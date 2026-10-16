#!/usr/bin/env python3
"""Read-only Pocket4 EUR/USD historical-bar and PAPER strategy-PnL auditor.

No IB connection, imports of broker clients, file mutation, or order authority.
Reads a stable snapshot of existing files and prints JSON to stdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


def decimal(value):
    try:
        v = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("INVALID_NUMERIC_VALUE") from None
    if not v.is_finite():
        raise ValueError("NONFINITE_NUMERIC_VALUE")
    return v


def load(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("UNSAFE_OR_MISSING_INPUT:" + str(path))
    def unique_pairs(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise ValueError("DUPLICATE_JSON_KEY:" + k)
            d[k] = v
        return d
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")), object_pairs_hook=unique_pairs)


def audit_pnl(report):
    if report.get("scope") != "STRATEGY_PERIOD_NOT_ACCOUNT_NAV":
        raise ValueError("UNEXPECTED_PNL_SCOPE")
    totals = report["totals"]
    components = report["components"]
    if not components:
        raise ValueError("EMPTY_COMPONENTS")
    keys = ("gross_total_pnl", "net_total_pnl", "fees_paid", "realized_gross_pnl", "unrealized_gross_pnl")
    for key in keys:
        actual = decimal(totals[key])
        computed = sum((decimal(item[key]) for item in components), Decimal(0))
        if abs(actual - computed) > Decimal("0.000001"):
            raise ValueError("TOTALS_MISMATCH:" + key)
    for item in components:
        if abs(decimal(item["gross_total_pnl"]) - decimal(item["fees_paid"]) - decimal(item["net_total_pnl"])) > Decimal("0.000001"):
            raise ValueError("COMPONENT_FEE_MISMATCH:" + str(item.get("instrument")))
    return {
        "period_id": report["period_id"],
        "observed_at_ms": report["observed_at_ms"],
        "currency": report["currency"],
        "gross_total_pnl": str(decimal(totals["gross_total_pnl"])),
        "fees_paid": str(decimal(totals["fees_paid"])),
        "net_total_pnl": str(decimal(totals["net_total_pnl"])),
        "account_all_in_pnl_known": report.get("all_in_account_net_pnl") is not None,
        "strategy_components": [{
            "instrument": item["instrument"],
            "position": str(item["position"]),
            "gross": str(decimal(item["gross_total_pnl"])),
            "fees": str(decimal(item["fees_paid"])),
            "net": str(decimal(item["net_total_pnl"])),
            "execution_count": item["execution_count"],
            "fee_coverage": item.get("fees_complete") is True,
        } for item in components],
    }


def audit_bars(dataset, expected_sha):
    if expected_sha is not None and len(expected_sha) != 64:
        raise ValueError("BAD_EXPECTED_SHA256")
    raw = dataset.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if expected_sha is not None and sha != expected_sha:
        raise ValueError("DATASET_SHA256_MISMATCH")
    # Input is a retained IB historical bar research artifact, not an executable
    # tick-accurate spread or a point-in-time forecasting evidence source.
    d = load(dataset)
    if not d.get("research_only_revised_not_contemporaneous"):
        raise ValueError("HISTORICAL_SOURCE_CLASSIFICATION_MISSING")
    rows = d["bars"]
    if not rows:
        raise ValueError("EMPTY_BARS")
    prev = None
    equal_close = 0
    identical_ohlc = 0
    for bar in rows:
        ts = bar["time"]
        if type(ts) is not int or (prev is not None and ts <= prev):
            raise ValueError("BAR_TIMESTAMP_NOT_STRICTLY_ASCENDING")
        prev = ts
        bid, ask = bar["bid"], bar["ask"]
        delta = decimal(ask["close"]) - decimal(bid["close"])
        if delta < 0:
            raise ValueError("CROSSED_CLOSE")
        equal_close += delta == 0
        identical_ohlc += bid == ask
    return {
        "dataset_sha256": sha,
        "bar_count": len(rows),
        "first_epoch_s": rows[0]["time"],
        "last_epoch_s": rows[-1]["time"],
        "zero_close_spread_count": equal_close,
        "zero_close_spread_fraction": equal_close / len(rows),
        "identical_bid_ask_ohlc_count": identical_ohlc,
        "history_not_executable_spread": True,
        "history_not_contemporaneous_prediction_evidence": True,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pnl", type=Path, required=True)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--dataset-sha256")
    args = p.parse_args()
    try:
        result = {"schema": "heptatrader.pocket4-readonly-audit.v1",
                  "trading_authority": False, "model_promotion": False,
                  "pnl": audit_pnl(load(args.pnl)),
                  "historical": audit_bars(args.dataset, args.dataset_sha256)}
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"schema": "heptatrader.pocket4-readonly-audit.v1",
                          "status": "REJECTED", "reason": str(exc)}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
