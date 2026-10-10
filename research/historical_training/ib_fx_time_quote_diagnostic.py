"""Offline comparison of IB server-second execution timestamps with signed PAPER quotes.

No historical OHLC is used as executable spread. This is NOT measured slippage:
IB execution server time has second granularity and the quote source is a
sampled read-only Tool Gateway feed, not a venue-stamped executable order book.
No Broker network calls and no trading/model-promotion authority.
"""
from __future__ import annotations
import argparse
import bisect
from copy import deepcopy
import json
import math
from pathlib import Path

from .common import EvidenceError, read, sha, digest, write_once
from .ib_fx_execution_time import CLIENT_ID, REQUEST_ID, _check_oms

MAX_PRECEDING_QUOTE_AGE_MS = 2000
MAX_MARK_AGE_MS = 5000
MAX_TIME_PRECISION_MS = 1000


def diagnostic_match(executions, quotes):
    """Match only quotes knowable by the BEGINNING of the IB server second."""
    if not isinstance(executions,list) or len(executions) > 2048:
        raise EvidenceError("SERVER_TIME_ROWS_BOUND")
    # IB corrected execution IDs differ only after their last period. A
    # returned correction chain is not multiple independent fills; until
    # its authoritative terminal correction is reconciled, refuse pricing.
    families={}
    for row in executions:
        ident=row.get("execution_id")
        if not isinstance(ident,str) or not ident:
            raise EvidenceError("SERVER_TIME_EXECUTION_ID_INVALID")
        family,sep,suffix=ident.rpartition(".")
        family_key=family if sep and suffix.isdecimal() else ident
        prior=families.get(family_key)
        if prior is not None and prior!=ident:
            raise EvidenceError("SERVER_TIME_CORRECTION_FAMILY_AMBIGUOUS")
        families[family_key]=ident
    ordered=sorted(quotes,key=lambda q:(q["known_at_ms"],q["t"]))
    arrival=[q["known_at_ms"] for q in ordered]
    matches=[]
    skip={"price_pending":0,"not_original_oms_matched":0,"quote_unavailable":0,
          "quote_stale":0}
    for row in executions:
        if (type(row.get("server_time_second_ms")) is not int
            or row.get("server_time_precision_ms") != 1000
            or row.get("side") not in ("BOT","SLD")):
            raise EvidenceError("SERVER_TIME_ROW_INVALID")
        if row.get("price_revision_pending") is True:
            skip["price_pending"] += 1
            continue
        if row.get("matched_exact_oms_economics") is not True:
            skip["not_original_oms_matched"] += 1
            continue
        fill_time=row["server_time_second_ms"]
        i=bisect.bisect_right(arrival,fill_time)-1
        if i<0:
            skip["quote_unavailable"]+=1
            continue
        q=ordered[i]
        if (not 0<=fill_time-q["known_at_ms"]<=MAX_PRECEDING_QUOTE_AGE_MS
            or not 0<=fill_time-q["t"]<=MAX_MARK_AGE_MS):
            skip["quote_stale"]+=1
            continue
        bid=float(q["bid"])
        ask=float(q["ask"])
        px=float(row["price"])
        if not all(math.isfinite(v) for v in (bid,ask,px)) or not 0<bid<=ask or px<=0:
            raise EvidenceError("SERVER_TIME_QUOTE_OR_EXECUTION_PRICE")
        mid=(ask+bid)/2
        # Negative is a favorable price deviation. The timestamp is
        # rounded to a second; this cannot validate actual execution slippage.
        adverse=(px-ask if row["side"]=="BOT" else bid-px)/mid*10000
        matches.append({"adverse_bps":adverse,
                        "preceding_quote_age_ms":fill_time-q["known_at_ms"],
                        "quoted_full_spread_bps":(ask-bid)/mid*10000})
    def percentile(values,p):
        if not values:
            return None
        arr=sorted(values)
        return round(arr[min(len(arr)-1,int((len(arr)-1)*p))],7)
    return {
        "broker_execution_rows":len(executions),
        "strict_preceding_quote_matches":len(matches),
        "excluded":skip,
        "apparent_price_deviation_bps":{
            "p10":percentile([x["adverse_bps"] for x in matches],.1),
            "p50":percentile([x["adverse_bps"] for x in matches],.5),
            "p90":percentile([x["adverse_bps"] for x in matches],.9)},
        "strict_quote_age_ms_p90":percentile([x["preceding_quote_age_ms"] for x in matches],.9),
        "quoted_full_spread_bps_p90":percentile([x["quoted_full_spread_bps"] for x in matches],.9),
        "execution_time_precision_ms":MAX_TIME_PRECISION_MS,
        "broker_server_time_not_exchange_time":True,
        "sampled_quote_not_attested_execution_book":True,
        "actual_slippage_verified":False,
        "forward_alpha_proven":False,
        "broker_requests_sent":0,
        "orders_sent":0,
    }


def analyze(config, period, oms_path, source_path):
    from laya_cell.resident_data import read_quotes
    from laya_cell.dataset import verify_source_prefixes
    from laya_cell.integrity import digest as live_digest
    receipt=read(source_path,8*1024*1024)
    if (receipt.get("schema") != "heptatrader.ib-paper-fx-execution-server-time.v1"
        or receipt.get("complete") is not True
        or receipt.get("broker_end_received") is not True
        or receipt.get("read_only") is not True
        or receipt.get("trade_authority") is not False
        or receipt.get("broker_orders_sent") != 0
        or receipt.get("outbound_counts") != {"71":1,"7":1}
        or receipt.get("broker_client_id") != CLIENT_ID
        or receipt.get("request_id") != REQUEST_ID
        or receipt.get("source_code_sha256") != sha(Path(__file__).with_name("ib_fx_execution_time.py"))
        or receipt.get("period_sha256") != sha(Path(config["period_path"]))
        or receipt.get("oms_sha256") != sha(oms_path)
        or receipt.get("started_at_ms") != period["started_at_ms"]
        or config["period_sha256"] != receipt["period_sha256"]
        or period.get("execution_domain") != "PAPER"):
        raise EvidenceError("SERVER_TIME_RECEIPT_SCOPE_OR_SOURCES_CHANGED")
    rows=receipt.get("returned_executions")
    if (not isinstance(rows,list) or len(rows)>2048
        or len(rows)!=receipt.get("broker_returned_rows")
        or len({r.get("execution_id") for r in rows})!=len(rows)):
        raise EvidenceError("SERVER_TIME_RECEIPT_ROWS_INVALID")
    original=deepcopy(rows)
    reconciliation=_check_oms(original,oms_path,period["account"],period["started_at_ms"])
    if receipt["reconciliation"]!=reconciliation:
        raise EvidenceError("SERVER_TIME_RECONCILIATION_CHANGED")
    now_ms=__import__("time").time_ns()//1_000_000
    quotes,pins=read_quotes(config,now_ms)
    verify_source_prefixes({"scope":config["period_sha256"]+":EUR.USD","source_prefixes":pins})
    if sha(oms_path)!=receipt["oms_sha256"] or sha(Path(config["period_path"]))!=receipt["period_sha256"]:
        raise EvidenceError("SERVER_TIME_SOURCE_CHANGED_DURING_READ")
    out=diagnostic_match(original,quotes)
    out.update({
        "schema":"heptatrader.ib-fx-server-time-quote-diagnostic.v1",
        "state":"RESEARCH_ONLY_NO_ACTUAL_SLIPPAGE_OR_PROMOTION",
        "period_sha256":receipt["period_sha256"],
        "broker_execution_receipt_sha256":sha(source_path),
        "oms_sha256":receipt["oms_sha256"],
        "original_live_quote_source_prefix_sha256":live_digest(pins),
        "original_live_quote_source_pins":pins,
        "diagnostic_source_sha256":sha(Path(__file__)),
        "original_live_quote_count":len(quotes),
        "source_prefix_count":len(pins),
        "historical_bar_bid_ask_used_for_spread":False,
        "trading_strategy_updated":False,
        "model_updated":False,
        "promotion_allowed":False,
    })
    return out

def main():
    ap=argparse.ArgumentParser()
    for name in ("config","period","oms","receipt","output"):
        ap.add_argument("--"+name,required=True,type=Path)
    a=ap.parse_args()
    if a.output.exists() or a.output.is_symlink():
        raise SystemExit("OUTPUT_EXISTS")
    c=read(a.config)
    p=read(a.period)
    result=analyze(c,p,a.oms,a.receipt)
    write_once(a.output,result)
    print(json.dumps({k:result[k] for k in (
        "state","broker_execution_rows","strict_preceding_quote_matches",
        "excluded","apparent_price_deviation_bps","actual_slippage_verified",
        "promotion_allowed","broker_requests_sent","orders_sent")},sort_keys=True))
if __name__=="__main__":
    main()
