"""Independent first-480-campaign original IB PAPER quote-gap forensics.

Strictly frozen SHA256-bound, read-only original event stream reconstruction.
This is not an IB digitally signed quote, root-cause attribution to the
broker, a trading fill, training sample, or production monitor promotion.
"""
from __future__ import annotations
import argparse
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import stat

SCHEMA="heptatrader.fx-original-local-quote-gap-forensics.v1"
ORIGINAL_PLAN_SHA="8a77da0fcdd3d251a19d53209fa7a836386848380f92e3681590b33ce2bf98d5"
CYCLE=0
STATUS_SHA="10287642a8d196cc7bbed8783dfa9b8fad2531495de10a74f49d090719e94292"
RECEIPT_SHA="7f2f88ca47d181a0a8415c427913a848834a487f8b731f8e9fa3183a4206226b"
PERIOD_SHA="61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
ALLOWED_KINDS=frozenset(("HEADER","QUOTE","FAILURE","END"))
MAX_PREFIX=64*1024*1024
MAX_STATUS=256*1024
MAX_LINES=120000
TZ=timezone(timedelta(hours=8))
class GapHold(ValueError):pass

def require(cause,code):
    if not cause:raise GapHold(code)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def strict(raw):
    def pairs(items):
        d={}
        for k,v in items:
            require(k not in d,"DUPLICATE_JSON_KEY")
            d[k]=v
        return d
    try:
        return json.loads(raw,object_pairs_hook=pairs,
                  parse_constant=lambda x: (_ for _ in ()).throw(GapHold("NONFINITE_JSON")))
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise GapHold("INVALID_JSON") from exc

def private_prefix(path, length, *, complete=False):
    p=Path(path)
    require(p.is_absolute() and not p.is_symlink()
            and ".." not in p.parts,"ORIGINAL_PREFIX_PATH_UNTRUSTED")
    require(type(length) is int and 0<length<=MAX_PREFIX,
            "ORIGINAL_PREFIX_BOUND")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode)
                and before.st_uid==os.geteuid()
                and before.st_nlink==1 and not before.st_mode&0o077
                and (before.st_size==length if complete else before.st_size>=length),
                "ORIGINAL_PREFIX_OWNER_INODE_SIZE")
        buf=bytearray()
        while len(buf)<length:
            part=os.pread(fd,min(1024*1024,length-len(buf)),len(buf))
            require(bool(part),"ORIGINAL_PREFIX_TRUNCATED")
            buf.extend(part)
        after=os.fstat(fd)
        require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
                ==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),
                "ORIGINAL_FILE_CHANGED_DURING_READ")
        return bytes(buf)
    finally:os.close(fd)

def read_frozen(path,*,max_bytes=MAX_STATUS):
    p=Path(path)
    require(p.is_file() and not p.is_symlink(),"FROZEN_FILE_NOT_PRIVATE")
    return private_prefix(p,p.stat().st_size,complete=True)

def parse_original_prefix(raw,period_sha):
    require(raw.endswith(b"\n") and len(raw)>100,"ORIGINAL_PREFIX_NOT_LINE_COMPLETE")
    lines=raw.splitlines()
    require(2<=len(lines)<=MAX_LINES,"ORIGINAL_PREFIX_LINE_BOUND")
    last_clock=0
    prior="0"*64
    header=None
    quotes=[]
    failures=[]
    terminal=False
    for index,line in enumerate(lines):
        require(0<len(line)<=65536,"ORIGINAL_RECORD_SIZE")
        row=strict(line)
        base={k:v for k,v in row.items() if k!="sha256"}
        require(row.get("schema")=="heptatrader.laya-shadow-quotes.v1"
                and type(row.get("sequence")) is int and row["sequence"]==index
                and row.get("previous")==prior
                and row.get("sha256")==sha(canonical(base)),
                "ORIGINAL_EVENT_CHAIN_INVALID")
        prior=row["sha256"]
        when=row.get("at_ms")
        require(type(when) is int and when>0 and
                (index==0 or when>=last_clock),"ORIGINAL_LOCAL_CLOCK_INVALID")
        last_clock=when
        kind=row.get("kind")
        data=row.get("data")
        require(kind in ALLOWED_KINDS and type(data) is dict
                and (not terminal),"ORIGINAL_EVENT_KIND_INVALID")
        if index==0:
            header=data
            require(kind=="HEADER"
                    and header.get("source")=="IB_TOOL_GATEWAY"
                    and header.get("instrument")=="EUR.USD"
                    and header.get("execution_domain")=="PAPER"
                    and header.get("period_sha256")==period_sha
                    and header.get("broker_orders_sent")==0
                    and header.get("write_operations_allowed")==[],
                    "ORIGINAL_HEADER_OR_PERIOD_CHANGED")
        elif kind=="QUOTE":
            event=data.get("observed_at_ms")
            expiry=data.get("stale_after_ms")
            require(data.get("source")=="IB" and data.get("authoritative") is True
                    and data.get("stale") is False
                    and type(event) is int and type(expiry) is int
                    and 0<event<=when<=min(expiry,event+5000),
                    "ORIGINAL_QUOTE_NOT_AUTHORITATIVE")
            try:
                bid=Decimal(str(data["bid"]))
                ask=Decimal(str(data["ask"]))
            except (ValueError,KeyError,InvalidOperation) as exc:
                raise GapHold("ORIGINAL_QUOTE_NUMERIC") from exc
            require(bid.is_finite() and ask.is_finite()
                    and 0<bid<=ask,"ORIGINAL_BID_ASK_INVALID")
            subscription=data.get("subscription_id")
            require(type(subscription) is str and bool(subscription),
                    "ORIGINAL_SUBSCRIPTION_UNKNOWN")
            quotes.append({"event":event,"available":when,"seq":index,
                           "bid":str(bid),"ask":str(ask),
                           "subscription_sha256":sha(subscription.encode()),
                           "status":data.get("subscription_state")})
        elif kind=="FAILURE":
            reason=data.get("reason")
            require(type(reason) is str and 1<=len(reason)<=100
                    and data.get("quote_timestamp_rewritten") is False,
                    "ORIGINAL_FAIL_CLAIM_CHANGED")
            failures.append({"at":when,"seq":index,"reason":reason})
        elif kind=="END":
            require(data.get("broker_orders_sent")==0,"ORIGINAL_END_HAS_ORDER")
            terminal=True
    return {"quotes":quotes,"failures":failures,
            "last_seq":len(lines)-1,"ended":terminal,"chain_head":prior,
            "header_collector_sha256":header.get("collector_sha256")}

def quote_gaps(events):
    require(events,"NO_FROZEN_QUOTES")
    by_t={}
    for q in events:
        group=by_t.setdefault(q["event"],{"earliest":q,"latest":q})
        require(group["earliest"]["bid"]==q["bid"] and
                group["earliest"]["ask"]==q["ask"],
                "SAME_TIMESTAMP_PRICE_CONFLICT")
        if q["available"]<group["earliest"]["available"]:
            group["earliest"]=q
        if q["available"]>group["latest"]["available"]:
            group["latest"]=q
    ts=sorted(by_t)
    return [(b-a,by_t[a]["latest"],by_t[b]["earliest"])
            for a,b in zip(ts,ts[1:])],len(ts)

def probe_matches(probes,before,after,events):
    """Exact frozen diagnosis, not a rewritable alternative health verdict."""
    require(before<after and after-before>30000,"NO_MATERIAL_ORIGINAL_SOURCE_GAP")
    times=sorted({q["event"] for q in events})
    counts=Counter()
    answers=[]
    for probe in probes:
        kind=probe.get("status")
        if kind=="USABLE":continue
        reason=None
        if kind=="ENDPOINT_GAP":
            due=probe.get("due_ms")
            require(type(due) is int,"ENDPOINT_CLOCK_NOT_PINNED")
            i=bisect_left(times,due)
            delta=times[i]-due if i<len(times) else None
            if (delta is not None and delta==probe.get("actual_delay_ms")
                    and delta>5000 and before<=due<after):
                reason="ENDPOINT_FIRST_ORIGINAL_QUOTE_LATE"
        elif kind=="PATH_GAP":
            anchor,due=probe.get("anchor_ms"),probe.get("due_ms")
            if (type(anchor) is int and type(due) is int
                    and anchor<after and due>before):
                reason="ACTUAL_ORIGINAL_EVENT_GAP_CROSSES_PATH"
        elif kind=="INPUT_UNAVAILABLE":
            start=probe.get("start_ms")
            if type(start) is int and before<=start<=after:
                reason="NO_ORIGINAL_EVENT_DURING_PROBE_INPUT"
        counts[kind]+=1
        answers.append({"probe_status":kind,"probe_start_ms":probe.get("start_ms"),
                        "matched_gap_evidence":reason is not None,
                        "diagnostic_reason":reason})
    return answers,dict(counts)

def audit(campaign_root,output):
    root=Path(campaign_root)
    plan_raw=read_frozen(root/"plan.json")
    receipt_raw=read_frozen(root/("cycle-%03d-receipt.json"%CYCLE))
    status_raw=read_frozen(root/("cycle-%03d-status.json"%CYCLE))
    require(sha(plan_raw)==ORIGINAL_PLAN_SHA
            and sha(receipt_raw)==RECEIPT_SHA and sha(status_raw)==STATUS_SHA,
            "FROZEN_CAMPAIGN_RECORD_CHANGED")
    plan,receipt,status=map(strict,(plan_raw,receipt_raw,status_raw))
    require(plan.get("period_sha256")==PERIOD_SHA and
            plan.get("installed_monitor_sha256") is not None and
            plan.get("trade_authority") is False and
            plan.get("production_monitor_modified") is False and
            plan.get("count")==480,"ORIGINAL_STUDY_PLAN_NOT_HELD")
    require(receipt.get("index")==CYCLE and
            receipt.get("state")=="CAPTURED_ORIGINAL_CACHE_HEALTH" and
            receipt.get("status_sha256")==STATUS_SHA and
            receipt.get("previous_receipt_sha256")=="0"*64 and
            receipt.get("original_reported_state")=="HOLD_NEW_FUSION_WINDOW" and
            receipt.get("source_ready") is False and
            receipt.get("publisher_eligible") is True and
            receipt.get("issues")==[] and receipt.get("broker_orders_sent")==0,
            "ORIGINAL_OBSERVER_OUTCOME_OR_AUTHORITY_CHANGED")
    require(status.get("schema")=="heptatrader.quote-readiness-status.v1"
            and status.get("period_sha256")==PERIOD_SHA and
            status.get("state")=="HOLD_NEW_FUSION_WINDOW" and
            status.get("at_ms")==receipt.get("original_asof_ms") and
            status.get("usable")==19 and status.get("unusable")==5 and
            status.get("orders_sent")==0 and
            status.get("trading_authority") is False and
            status.get("automatic_trial_registration") is False,
            "ORIGINAL_HEALTH_SCOPE_CHANGED")
    probes=status.get("probes")
    require(type(probes) is list and len(probes)==24,"ORIGINAL_HEALTH_PROBES_MISSING")
    counted=dict(Counter(r.get("status") for r in probes))
    require(counted==status.get("counts") and
            status["usable"]==counted.get("USABLE"),
            "ORIGINAL_HEALTH_COUNTS_CHANGED")
    prefixes=status.get("source_prefixes")
    require(type(prefixes) is list and
            sha(canonical(prefixes))==status.get("source_prefix_digest"),
            "SOURCE_PREFIX_LIST_HASH_CHANGED")
    original_root=Path(plan["original_feed_dir"])
    require(original_root.is_absolute() and original_root.is_dir()
            and not original_root.is_symlink(),"REGISTERED_FEED_ROOT_CHANGED")
    bound=[]
    all_quotes=[]
    all_failures=[]
    for ref in prefixes:
        path=Path(ref["path"])
        require(path.is_relative_to(original_root),"UNEXPECTED_SOURCE_PATH")
        frozen=private_prefix(path,ref["bytes"])
        require(sha(frozen)==ref["sha256"],"ORIGINAL_PREFIX_BYTES_REPLACED")
        if path.name!="quotes.jsonl":continue
        parsed=parse_original_prefix(frozen,PERIOD_SHA)
        require(parsed["ended"] is ref.get("ended") and
                parsed["chain_head"]==ref["chain_head"],
                "ORIGINAL_SOURCE_CHAIN_HEAD_CHANGED")
        bound.append({"segment":path.parent.name,
                      "prefix_bytes":ref["bytes"],
                      "prefix_sha256":ref["sha256"],
                      "ended_at_snapshot":parsed["ended"],
                      "quote_records":len(parsed["quotes"]),
                      "failure_records":len(parsed["failures"])})
        all_quotes.extend(parsed["quotes"])
        all_failures.extend(parsed["failures"])
    require(1<=len(bound)<=8,"QUOTE_SEGMENT_SET_UNQUALIFIED")
    asof=status["at_ms"]
    all_quotes=[q for q in all_quotes if q["available"]<=asof]
    all_failures=[f for f in all_failures if f["at"]<=asof]
    failures=[p for p in probes if p.get("status")!="USABLE"]
    require(len(failures)==5 and counted==
            {"USABLE":19,"ENDPOINT_GAP":2,"PATH_GAP":3},
            "UNEXPECTED_FROZEN_FIRST_PROBE_STATES")
    start=min(p["start_ms"] for p in failures)-180000
    stop=max(p["due_ms"] for p in failures)+180000
    active=[q for q in all_quotes if start<=q["event"]<=stop]
    gaps,distinct=quote_gaps(active)
    require(gaps,"SOURCE_GAP_NOT_REPRODUCIBLE")
    largest=max(gaps,key=lambda item:item[0])
    gap,before,after=largest
    require(gap>30000 and before["event"]<after["event"],
            "ORIGINAL_MATERIAL_GAP_NOT_REPRODUCED")
    cases,counts=probe_matches(probes,before["event"],after["event"],active)
    matched=sum(p["matched_gap_evidence"] for p in cases)
    require(matched==len(cases)==5,"FROZEN_PROBE_PATH_NOT_EXPLAINED")
    # The local sequence is continuous through many FAILURE rows.
    primary=[ref for ref in bound if ref["segment"].startswith("segment-1791882475")]
    require(len(primary)==1,"FIRST_GAP_SEGMENT_IDENTITY_CHANGED")
    segment_name=primary[0]["segment"]
    fpath=original_root/segment_name/"quotes.jsonl"
    raw=private_prefix(fpath,primary[0]["prefix_bytes"])
    parsed=parse_original_prefix(raw,PERIOD_SHA)
    orig_before=[q for q in parsed["quotes"] if q["event"]==before["event"]]
    orig_after=[q for q in parsed["quotes"] if q["event"]==after["event"]]
    require(orig_before and orig_after,"GAP_NOT_WITHIN_ONE_ORIGINAL_SEGMENT")
    bef=max(orig_before,key=lambda q:q["seq"])
    aft=min(orig_after,key=lambda q:q["seq"])
    require(bef["seq"]<aft["seq"],"LOCAL_SEQUENCE_NOT_MONOTONIC")
    subset=[f for f in parsed["failures"] if bef["seq"]<f["seq"]<aft["seq"]]
    errors=dict(Counter(f["reason"] for f in subset))
    require(len(subset)>0 and
            aft["seq"]-bef["seq"]-1==len(subset),
            "FAILURE_SERIES_NOT_CONTIGUOUS")
    report={
        "schema":SCHEMA,"snapshot_cycle":CYCLE,
        "frozen_original_plan_sha256":ORIGINAL_PLAN_SHA,
        "frozen_original_receipt_sha256":RECEIPT_SHA,
        "frozen_original_health_sha256":STATUS_SHA,
        "original_snapshot_at_ms":asof,
        "original_stored_verdict":"HOLD_NEW_FUSION_WINDOW",
        "source_prefixes_rechecked_full_bytes":len(prefixes),
        "original_quote_segments":bound,
        "source_prefix_digest_reconstructed":sha(canonical(prefixes)),
        "disjoint_original_probe_counts":counted,
        "failed_probe_count":len(cases),"failed_probes_explained_by_same_gap":matched,
        "failed_probe_evidence":cases,
        "gap":{
            "event_time_gap_ms":gap,
            "quote_before_event_ms":before["event"],
            "quote_after_event_ms":after["event"],
            "quote_before_available_ms":bef["available"],
            "quote_after_available_ms":aft["available"],
            "local_collector_delivery_gap_ms":aft["available"]-bef["available"],
            "both_quotes_within_same_sha_chained_segment":True,
            "segment_name":segment_name,
            "quote_before_sequence":bef["seq"],
            "quote_after_sequence":aft["seq"],
            "intervening_failure_count":len(subset),
            "intervening_failure_reason_counts":errors,
            "subscription_identity_changed":bef["subscription_sha256"]!=aft["subscription_sha256"],
            "subscription_before_sha256":bef["subscription_sha256"],
            "subscription_after_sha256":aft["subscription_sha256"],
            "collector_failure_events_are_NOT_IB_signed":True,
        },
        "retrospective_original_read_only":True,
        "not_evidence_of_market_closure_or_upstream_root_cause":True,
        "not_a_frozen_study_requalification":True,
        "original_hold_remains_hold":True,
        "production_monitor_or_trade_modified":False,
        "model_fit_or_promotion_authorized":False,
        "independent_broker_signed_quote_or_witness":False,
        "broker_queries_or_orders_sent":0,
        "trade_authority":False,
    }
    out=Path(output)
    require(out.is_absolute() and not out.exists() and not out.is_symlink(),
            "OUTPUT_ALREADY_USED")
    out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    require(stat.S_IMODE(out.parent.stat().st_mode)==0o700
            and out.parent.stat().st_uid==os.geteuid(),"OUTPUT_PARENT_NOT_PRIVATE")
    fd=os.open(out,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as h:
        h.write(canonical(report)+b"\n");h.flush();os.fsync(h.fileno())
    d=os.open(out.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(d)
    finally:os.close(d)
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--campaign",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    r=audit(a.campaign,a.output)
    g=r["gap"]
    print(json.dumps({"state":"ORIGINAL_QUOTE_GAP_AND_FAILURE_CODES_PROVEN_HOLD",
         "original_gap_ms":g["event_time_gap_ms"],
         "intervening_failures":g["intervening_failure_count"],
         "failed_probes_explained":r["failed_probes_explained_by_same_gap"],
         "original_health_verdict":"HOLD_NEW_FUSION_WINDOW",
         "broker_orders_sent":0,
         "promotion_allowed":False},sort_keys=True))
if __name__=="__main__":main()
