"""Evidence-only execution cost calibration. NEVER dispatches Broker requests.

IB historical BID/ASK OHLC bars are NOT contemporaneous executable top-of-book
quotes. Original signed PAPER live quote receipts calibrate spread; deduplicated
IB commission callbacks calibrate fees. The event timestamp is a callback arrival
time, NOT an attested exchange fill time, so matched slippage is diagnostic only.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import mean, median

def quantile(values, p):
    if not values:
        return None
    sorted_values = sorted(values)
    index = min(len(values)-1, int((len(values)-1)*p))
    return round(sorted_values[index], 7)


def _safe_number(value):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError("NONFINITE_BROKER_ECONOMICS")
    return value


def _parse_oms_events(path, account, period_start_ms):
    """Keep full raw OMS integrity hashes; don't print identifiers or accounts."""
    from laya_cell.shadow_source import strict
    executions, fees, conflicts = {}, {}, set()
    hash_ = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for i, line in enumerate(stream):
            if i >= 250000 or len(line) > 65536 or not line.endswith(b"\n"):
                raise ValueError("OMS_JOURNAL_BOUNDS")
            hash_.update(line)
            ev = strict(line)
            if ev.get("execution_domain") != "PAPER":
                continue
            typ = ev.get("event")
            if typ not in ("broker_execution","broker_commission"):
                continue
            identity = ev.get("broker_execution_id")
            if not isinstance(identity,str) or not identity or len(identity)>128:
                continue
            if typ=="broker_execution":
                # Exact PAPER accounting account and reset boundary, not a
                # global mixed-account callback journal.
                if ev.get("account") != account or ev.get("ts_ms",0) < period_start_ms:
                    continue
                row=(ev.get("instrument"),ev.get("side"),ev.get("qty"),ev.get("price"))
                if identity in executions and executions[identity][0]!=row:
                    conflicts.add(identity)
                if identity not in executions:
                    executions[identity]=(row,ev.get("ts_ms"))
            else:
                row=(ev.get("broker_econ_commission"),ev.get("broker_econ_commission_currency"),
                     ev.get("broker_econ_valid"),ev.get("broker_econ_version"))
                if identity in fees and fees[identity]!=row:
                    conflicts.add(identity)
                fees.setdefault(identity,row)
    return executions,fees,conflicts,hash_.hexdigest()


def calibrate(config,oms_path):
    from laya_cell.resident_data import read_quotes
    from laya_cell.integrity import digest,sha_file
    at=__import__('time').time_ns()//1_000_000
    ordered,pins=read_quotes(config,at)
    period=__import__('json').loads(Path(config["period_path"]).read_bytes())
    if (period.get("execution_domain")!="PAPER" or
        not isinstance(period.get("account"),str) or not period["account"] or
        type(period.get("started_at_ms")) is not int):
        raise ValueError("PAPER_PERIOD_FOR_COST_REQUIRED")
    start_sha=sha_file(Path(oms_path))
    execs,fees,conflicts,oms_sha=_parse_oms_events(
        oms_path,period["account"],period["started_at_ms"])
    if start_sha!=oms_sha or sha_file(Path(oms_path))!=oms_sha:
        raise ValueError("OMS_CHANGED_DURING_COST_READ")
    if conflicts:
        raise ValueError("CONFLICTING_DUPLICATE_EXECUTION_OR_COMMISSION")
    spread_bps=[]
    for quote in ordered:
        bid=float(quote["bid"]);ask=float(quote["ask"])
        if not 0<bid<=ask:
            raise ValueError("INVALID_LIVE_BID_ASK")
        mid=(bid+ask)/2
        spread_bps.append((ask-bid)/mid*10000)
    by_time=sorted(ordered,key=lambda x:(x["known_at_ms"],x["t"]))
    known=[q["known_at_ms"] for q in by_time]
    matched=[]
    valid_fees=[]
    gross_reported=0
    candidates=0
    excluded_fee=0
    for identity,(row,ts) in execs.items():
        instrument,side,qty,price=row
        if instrument!="EUR.USD":continue
        candidates+=1
        fee=fees.get(identity)
        if (fee is None or fee[1]!="USD" or fee[2]!=1
            or type(fee[0]) not in (int,float) or fee[0]<0
            or not math.isfinite(fee[0])):
            excluded_fee+=1
            continue
        qty=_safe_number(qty);price=_safe_number(price)
        if qty<=0 or price<=0:raise ValueError("BAD_BROKER_EXECUTION_ECONOMICS")
        fee_bps=fee[0]/(qty*price)*10000
        valid_fees.append(fee_bps)
        gross_reported+=fee[0]
        if type(ts) is not int or side not in ("BOT","SLD","BUY","SELL"):
            continue
        ix=bisect.bisect_right(known,ts)-1
        if ix<0:continue
        q=by_time[ix]
        if (q["t"]>ts or not 0<=ts-q["known_at_ms"]<=2000
            or not 0<=ts-q["t"]<=5000):
            continue
        bid=float(q["bid"]);ask=float(q["ask"]);mid=(bid+ask)/2
        adverse=(price-ask if side in ("BOT","BUY") else bid-price)/mid*10000
        matched.append({"quote_lag_ms":ts-q["known_at_ms"],"signed_adverse_bps":adverse,
                        "quote_spread_bps":(ask-bid)/mid*10000,
                        "fee_one_way_bps":fee_bps})
    return {
        "schema":"heptatrader.ib-cost-evidence.offline.v1",
        "instrument":"EUR.USD",
        "period_source_sha256":sha_file(Path(config["period_path"])),
        "period_started_at_ms":period["started_at_ms"],
        "exact_paper_account_scoped":True,
        "source":"ORIGINAL_CHAIN_VERIFIED_PAPER_LIVE_QUOTES_AND_OMS_COMMISSION_CALLBACKS",
        "source_prefix_digest":digest(pins),
        "source_prefix_count":len(pins),
        "oms_sha256":oms_sha,
        "quote_count":len(ordered),
        "quote_spread_full_bps":{
            "p50":quantile(spread_bps,.5),"p90":quantile(spread_bps,.9),
            "p95":quantile(spread_bps,.95),"p99":quantile(spread_bps,.99)},
        "fx_unique_executions":candidates,
        "fx_fee_complete_unique_executions":len(valid_fees),
        "fx_fee_missing_or_unproved_executions":excluded_fee,
        "fx_commission_total_usd":round(gross_reported,6),
        "fx_commission_one_way_bps":{
            "p50":quantile(valid_fees,.5),"p90":quantile(valid_fees,.9),
            "p95":quantile(valid_fees,.95),"p99":quantile(valid_fees,.99)},
        "causally_preceding_quote_matches":len(matched),
        "matched_quote_lag_ms_p90":quantile([x["quote_lag_ms"] for x in matched],.9),
        "callback_time_slippage_proxy_bps":{
            "p50":quantile([x["signed_adverse_bps"] for x in matched],.5),
            "p90":quantile([x["signed_adverse_bps"] for x in matched],.9),
            "p95":quantile([x["signed_adverse_bps"] for x in matched],.95)},
        "actual_fill_timestamp_attested":False,
        "actual_slippage_verified":False,
        "historical_bar_bid_ask_used_as_executable_spread":False,
        "commission_callbacks_are_reported_not_broker_flex_statements":True,
        "trade_authority":False,
        "broker_requests_sent":0,
        "orders_sent":0,
    }

if __name__=="__main__":
    import argparse
    from laya_cell.integrity import read_json
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("--oms",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    result=calibrate(read_json(args.config),args.oms)
    args.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with args.output.open('x') as fd:
        fd.write(json.dumps(result,sort_keys=True,indent=2)+'\n')
        fd.flush()
        os.fsync(fd.fileno())
    args.output.chmod(0o600)
    print(json.dumps({k:v for k,v in result.items() if k not in ('oms_sha256','source_prefix_digest')}))
