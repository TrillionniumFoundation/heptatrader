"""X230 standalone TEST-ONLY independent recomputation of one FX midprice label.

Reads a private COPY of Pocket4's original locally hash-chained IB PAPER quote
file, plus the precommitted shadow decision/feature seal and later result.
Generates ephemeral memory-only test Ed25519 key AFTER checking real source
chain, due time, prices, causal inputs and outcome independently. This is not
a separate IB market feed, enterprise-controlled production Observer,
real brokerage execution/PnL or authority to promote.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import time

DOMAIN=b"HEPTATRADER.TRAIN_V2.REAL_MARKET_LOCAL_SHADOW_X230_TEST_ONLY.V1\x00"
SCHEMA="heptatrader.train-v2-x230-independently-recomputed-original-quote-label.v1"
ROOT="heptatrader.train-v2-live-original-shadow-forward-price-outcome.v1"


def canonical(value)->bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def sha(raw:bytes)->str:
    return hashlib.sha256(raw).hexdigest()


def read_private(path:Path,max_bytes=5*1024*1024)->bytes:
    path=Path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid()
                or st.st_mode&0o077 or st.st_size>max_bytes):
            raise ValueError("REMOTE_COPY_NOT_PRIVATE_OR_BOUNDED")
        parts=[]
        remaining=st.st_size
        while remaining:
            part=os.read(fd,min(remaining,1024*1024))
            if not part:raise ValueError("COPY_TRUNCATED")
            parts.append(part);remaining-=len(part)
        return b"".join(parts)
    finally:
        os.close(fd)


def read_json(path:Path)->dict:
    return json.loads(read_private(path,131072))


def decimal(value)->Decimal:
    d=Decimal(str(value))
    if not d.is_finite():raise ValueError("SOURCE_NONFINITE")
    return d


def rounded(value:Decimal)->str:
    return str(value.quantize(Decimal("0.00000001")))


def verify_segment(quote_path:Path,original_prefix_sha:str,period_sha:str,
                   observed_no_later_than_ms:int):
    raw=read_private(quote_path)
    if not raw.endswith(b"\n"):raise ValueError("ORIGINAL_SOURCE_INCOMPLETE_TAIL")
    prior="0"*64
    aggregate=hashlib.sha256()
    prefix_found=False
    latest_at=0
    by_timestamp={}
    for index,line in enumerate(raw.splitlines(keepends=True)):
        if len(line)>65536 or line[-1:]!=b"\n":raise ValueError("SOURCE_ROW_BOUND")
        row=json.loads(line)
        body={k:v for k,v in row.items() if k!="sha256"}
        if (row.get("schema")!="heptatrader.laya-shadow-quotes.v1"
                or row.get("sequence")!=index or row.get("previous")!=prior
                or row.get("sha256")!=sha(canonical(body))
                or type(row.get("at_ms")) is not int
                or not latest_at<=row["at_ms"]<=observed_no_later_than_ms):
            raise ValueError("X230_ORIGINAL_QUOTE_CHAIN_INVALID")
        latest_at=row["at_ms"]
        prior=row["sha256"]
        aggregate.update(line)
        if aggregate.hexdigest()==original_prefix_sha:
            prefix_found=True
        kind=row["kind"]
        if index==0:
            header=row["data"]
            if (kind!="HEADER"
                    or header.get("source")!="IB_TOOL_GATEWAY"
                    or header.get("execution_domain")!="PAPER"
                    or header.get("instrument")!="EUR.USD"
                    or header.get("period_sha256")!=period_sha
                    or header.get("write_operations_allowed")!=[]
                    or header.get("broker_orders_sent")!=0):
                raise ValueError("X230_ORIGINAL_SOURCE_WRONG_AUTHORITY")
        elif kind=="QUOTE":
            obj=row["data"]
            t=obj.get("observed_at_ms")
            deadline=obj.get("stale_after_ms")
            if (obj.get("instrument")!="EUR.USD"
                    or obj.get("source")!="IB"
                    or obj.get("authoritative") is not True
                    or obj.get("stale") is not False
                    or type(t) is not int or type(deadline) is not int
                    or not t<=row["at_ms"]<=min(deadline,t+5000)):
                raise ValueError("X230_ORIGINAL_QUOTE_EXPIRED_OR_UNTRUSTED")
            bid=decimal(obj.get("bid"));ask=decimal(obj.get("ask"))
            if not Decimal(0)<bid<=ask:
                raise ValueError("X230_ORIGINAL_SOURCE_CROSSED_OR_NEGATIVE")
            entry={"event_at_ms":t,"available_at_ms":row["at_ms"],
                   "valid_until_ms":min(deadline,t+5000),
                   "bid":bid,"ask":ask,"sha256":row["sha256"]}
            old=by_timestamp.get(t)
            if old is not None and (old["bid"]!=bid or old["ask"]!=ask):
                raise ValueError("X230_DUPLICATE_SOURCE_PRICE_CONFLICT")
            if old is None or row["at_ms"]<old["available_at_ms"]:
                by_timestamp[t]=entry
        elif kind not in ("FAILURE","END"):
            raise ValueError("X230_ORIGINAL_SOURCE_KIND")
    if not prefix_found:
        raise ValueError("X230_ORIGINAL_OUTCOME_PREFIX_CHANGED")
    return [by_timestamp[t] for t in sorted(by_timestamp)],sha(raw),len(raw)


def verify_observation(root:Path)->dict:
    root=Path(root)
    decision=read_json(root/"decision.json")
    pre=read_json(root/"preoutcome.json")
    outcome=read_json(root/"outcome.json")
    spec=read_json(root/"spec.json")
    original=read_private(root/"decision.json",131072)
    frozen=read_private(root/"preoutcome.json",131072)
    if (decision.get("status")!="RESEARCH_SHADOW_PREACTION_ONLY"
            or decision.get("trade_authority") is not False
            or outcome.get("schema")!=ROOT
            or outcome.get("actual_endpoint_or_censoring_status")!="OBSERVED"
            or outcome.get("economic_gross_or_net_trading_pnl_proven") is not False
            or outcome.get("precommitted_decision_sha256")!=sha(original)
            or outcome.get("pre_outcome_frozen_feature_receipt_sha256")!=sha(frozen)
            or outcome.get("feature_id_revalidated_exactly")!=pre.get("full_feature_id")
            or decision.get("local_prospective_plan_id")!=outcome.get("exact_plan_id")
            or spec.get("period_sha256")!=pre.get("original_period_sha256")
            or spec.get("horizon_ms")!=300000
            or spec.get("label_tolerance_ms")!=5000
            or decision.get("outcome_not_available_before_ms")!=outcome.get("outcome_due_ms")):
        raise ValueError("X230_FORWARDED_RESEARCH_PRECOMMIT_INVALID")
    now=time.time_ns()//1000000
    if (now<outcome["evaluated_at_ms"]
            or outcome["evaluated_at_ms"]<outcome["outcome_due_ms"]
            or pre["captured_at_ms"]>=outcome["outcome_due_ms"]):
        raise ValueError("X230_OUTCOME_NOT_MATURE_OR_SOURCE_PRESEALED_TOO_LATE")
    quotes,source_sha,source_bytes=verify_segment(
        root/"original-quotes.jsonl",
        outcome["post_outcome_source_prefix_sha256_not_decision_time_source_hash"],
        spec["period_sha256"],now)
    decision_at=decision["decision_at_ms"]
    earlier=[q for q in quotes if q["event_at_ms"]<=decision_at
             and q["available_at_ms"]<=decision_at]
    history=earlier[-spec["lookback"]:]
    if (len(history)!=spec["lookback"]
            or [q["sha256"] for q in history]!=decision["source_history_quote_receipts"]
            or history[-1]["sha256"]!=outcome["original_quote_entry_sha256"]
            or decision_at-history[-1]["event_at_ms"]>spec["max_age_ms"]
            or history[-1]["valid_until_ms"]<decision_at
            or any(b["event_at_ms"]-a["event_at_ms"]>spec["max_gap_ms"]
                   for a,b in zip(history,history[1:]))):
        raise ValueError("X230_ORIGINAL_PREACTION_HISTORY_NOT_CAUSAL")
    mids=[(q["bid"]+q["ask"])/2 for q in history]
    spreads=[(q["ask"]-q["bid"])/mid*Decimal(10000)
             for q,mid in zip(history,mids)]
    features={
        "mid":rounded(mids[-1]),
        "spread_bps":rounded(spreads[-1]),
        "return_1_bps":rounded((mids[-1]/mids[-2]-1)*Decimal(10000)),
        "return_window_bps":rounded((mids[-1]/mids[0]-1)*Decimal(10000)),
    }
    if features!=pre["input_features"]["values"]:
        raise ValueError("X230_ORIGINAL_PRECOMMITTED_FEATURES_CHANGED")
    due=outcome["outcome_due_ms"]
    future=[q for q in quotes if due<=q["event_at_ms"]<=due+5000
            and q["available_at_ms"]<=outcome["evaluated_at_ms"]]
    if not future:raise ValueError("X230_SOURCE_ENDPOINT_UNAVAILABLE")
    terminal=future[0]
    endpoint=outcome["actual_original_endpoint_quote_receipt"]
    if (terminal["sha256"]!=endpoint["source_id"]
            or terminal["event_at_ms"]!=endpoint["event_at_ms"]
            or terminal["available_at_ms"]!=endpoint["available_at_ms"]):
        raise ValueError("X230_ENDPOINT_RECEIPT_NOT_EXACT_ORIGINAL")
    path=[q for q in quotes if history[-1]["event_at_ms"]<=q["event_at_ms"]
          <=terminal["event_at_ms"] and q["available_at_ms"]<=outcome["evaluated_at_ms"]]
    if any(b["event_at_ms"]-a["event_at_ms"]>spec["max_gap_ms"]
           for a,b in zip(path,path[1:])):
        raise ValueError("X230_PRICE_PATH_HAS_GAP")
    mid_end=(terminal["bid"]+terminal["ask"])/2
    result=rounded((mid_end/mids[-1]-1)*Decimal(10000))
    if result!=outcome["actual_market_mid_price_return_bps"]:
        raise ValueError("X230_PRICE_RETURN_CHANGED_FROM_ORIGINAL_OBSERVATION")
    return {
        "schema":SCHEMA,
        "signed_on_physical_host":socket.gethostname(),
        "role":"SEPARATE_HOST_TEST_ONLY_OBSERVER",
        "signature_scope":"POST_HORIZON_RECOMPUTED_POCKET4_ORIGINAL_QUOTE_CHAIN_COPY",
        "decision_precommit_sha256":sha(original),
        "frozen_preoutcome_sha256":sha(frozen),
        "reported_outcome_sha256":sha(read_private(root/"outcome.json",131072)),
        "copied_original_source_sha256":source_sha,
        "copied_original_source_bytes":source_bytes,
        "original_observer_quote_receipt":terminal["sha256"],
        "original_input_quote_receipts":[q["sha256"] for q in history],
        "frozen_feature_id":pre["full_feature_id"],
        "decision_at_ms":decision_at,
        "due_at_ms":due,
        "endpoint_at_ms":terminal["event_at_ms"],
        "reported_at_ms":outcome["evaluated_at_ms"],
        "verified_quote_path_points":len(path),
        "recomputed_five_minute_midprice_return_bps":result,
        "observer_computed_at_ms":now,
        "source_is_a_copy_of_pocket4_local_chain_not_separate_ib_feed":True,
        "production_independent_authority":False,
        "real_trade_fills_or_net_pnl_verified":False,
        "model_promotion_or_trading_authorized":False,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    os.umask(0o077)
    body=verify_observation(args.root)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    # Generated in memory on the Observer's host, never saved or exported.
    private=Ed25519PrivateKey.generate()
    material=DOMAIN+canonical(body)
    sig=private.sign(material)
    record={
        "envelope_schema":"heptatrader.train-v2-x230-observer-ephemeral-testonly-signed.v1",
        "signed_observation":body,
        "public_key_hex":private.public_key().public_bytes(
            Encoding.Raw,PublicFormat.Raw).hex(),
        "native_product_root_trust_enrolled":False,
        "signature_domain_hex":DOMAIN.hex(),
        "signature_hex":sig.hex(),
        "test_private_key_generated_in_x230_memory_only":True,
        "real_production_private_key_used":False,
    }
    raw=canonical(record)+b"\n"
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    d=os.open(args.output.parent,os.O_DIRECTORY|os.O_RDONLY)
    try:os.fsync(d)
    finally:os.close(d)
    print(json.dumps({"role":body["role"],"signing_host":body["signed_on_physical_host"],
                      "independently_recomputed_return_bps":
                         body["recomputed_five_minute_midprice_return_bps"],
                      "source_quote_path_points":body["verified_quote_path_points"],
                      "signature_receipt_sha256":sha(raw),"production_authority":False},
                      sort_keys=True))


if __name__=="__main__":
    main()
