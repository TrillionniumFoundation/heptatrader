"""Independent *research-only* original FX future-campaign audit.

Hard-pinned pocket4 plan/seal/code copied to qian-qi before first outcome.
Checks original source SHA chains, causal 13 pseudo-closes, frozen cost math,
single earliest future quote and quote-side price arithmetic. Never a broker
query, trade, real PnL, licensed IB market signature or product witness.
"""
from __future__ import annotations
import argparse
from decimal import Decimal, localcontext, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

SCHEMA="heptatrader.fx-original-future-readonly-independent-audit.v1"
RESEARCH="heptatrader.fx-original-future-price-cost-calendar.v1"
PLAN_PIN="02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04"
SEAL_PIN="1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b"
FROZEN_CODE_PIN="e08d952e80423a0fdd296f37c5d2d6efbdfb64ebd246ff03e00901f3465e2411"
HORIZONS=(15,30,60,120)
STEP=195*60000
PERIOD_SHA="61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
MAX_RECORD=256*1024
MAX_SOURCE_PREFIX=16*1024*1024

class AuditHold(ValueError): pass
def need(condition,reason):
    if not condition:raise AuditHold(reason)
def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def digest(raw):
    return hashlib.sha256(raw).hexdigest()
def strict(raw):
    def pairs(rows):
        result={}
        for k,v in rows:
            need(k not in result,"DUPLICATE_JSON_KEY")
            result[k]=v
        return result
    return json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda c:(_ for _ in ()).throw(AuditHold("NONFINITE_JSON")))
def source_decimal(value):
    try:
        need(type(value) is not bool and value is not None,"DECIMAL_INPUT_TYPE")
        d=Decimal(str(value))
    except (InvalidOperation,ValueError) as ex:
        raise AuditHold("INVALID_PRICE") from ex
    need(d.is_finite() and 0<d<Decimal("1e12"),"INVALID_PRICE_RANGE")
    return d
def exact_decimal(value):
    try:
        need(type(value) in (int,float,str) and not isinstance(value,bool),"DECIMAL_OUTPUT_TYPE")
        d=Decimal(str(value))
    except (ValueError,InvalidOperation) as ex:
        raise AuditHold("INVALID_RETURN_NUMBER") from ex
    need(d.is_finite() and abs(d)<Decimal("1000000"),"RETURN_NONFINITE_OR_OVERSIZE")
    return d

def private_file(path,cap=MAX_RECORD):
    p=Path(path)
    need(p.is_absolute() and not p.is_symlink(),"EVIDENCE_PATH_NOT_ABSOLUTE_OR_SYMLINK")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
             and st.st_nlink==1 and not st.st_mode&0o077
             and 0<st.st_size<=cap,"PRIVATE_FILE_NOT_ACCEPTABLE")
        chunks=[]
        remaining=st.st_size
        while remaining:
            b=os.read(fd,min(1024*1024,remaining))
            need(bool(b),"READ_SHORT")
            chunks.append(b);remaining-=len(b)
        after=os.fstat(fd)
        need((st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)==
             (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),
             "FILE_CHANGED_DURING_READ")
        return b"".join(chunks)
    finally:os.close(fd)

def evidence(root,name,max_bytes=MAX_RECORD):
    raw=private_file(Path(root)/name,max_bytes)
    return strict(raw),digest(raw)
def private_root(path):
    p=Path(path)
    need(p.is_absolute() and not p.is_symlink() and ".." not in p.parts,
         "ROOT_PATH_NOT_PRIVATE")
    st=p.stat()
    need(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid()
         and stat.S_IMODE(st.st_mode)==0o700,"ROOT_DIR_NOT_PRIVATE")
    return p

def frozen_plan(root,source_path):
    root=private_root(root)
    plan,p_sha=evidence(root,"plan.json")
    seal,s_sha=evidence(root,"sealed.json")
    need((p_sha,s_sha)==(PLAN_PIN,SEAL_PIN),"INDEPENDENT_PRECOMMIT_HASH_CHANGED")
    source_path=Path(source_path)
    need(source_path.is_absolute() and not source_path.is_symlink()
         and source_path.is_file() and source_path.stat().st_size<=128*1024
         and source_path.stat().st_uid==os.geteuid(),
         "FROZEN_RUNNER_SOURCE_FILE_UNSAFE")
    need(digest(source_path.read_bytes())==
         FROZEN_CODE_PIN==plan.get("research_code_sha256"),
         "FROZEN_RUNNER_SOURCE_CHANGED")
    need(seal.get("plan_sha256")==PLAN_PIN
        and seal.get("research_code_sha256")==FROZEN_CODE_PIN
        and seal.get("schema")==RESEARCH+".sealed.v1"
        and plan.get("schema")==RESEARCH
        and plan.get("period_sha256")==PERIOD_SHA
        and plan.get("independent_horizons_ms")==[h*60000 for h in HORIZONS]
        and plan.get("fixed_windows")==4
        and plan.get("grid_ms")==300000
        and plan.get("spacing_ms")==STEP
        and plan.get("first_decision_offset_ms")==1000
        and plan.get("last_decision_offset_ms")==4500
        and plan.get("source_minimum_usable")==20
        and plan.get("source_maximum_missing")==4
        and plan.get("source_freshness_age_max_ms")==30000
        and plan.get("readiness_file","").startswith("/home/alex/.local/state/")
        and plan.get("behavior_policy")=="NO_TRADE"
        and plan.get("not_native_train_v2_120min_spec") is True
        and plan.get("automatic_training") is False
        and plan.get("trade_authority") is False
        and plan.get("broker_orders_sent")==0
        and plan.get("model_promotion_allowed") is False,
        "PROTOCOL_CHANGED_FROM_PINNED_NO_TRADE")
    first=plan["anchors_ms"][0]
    need(first%300000==0 and type(plan["registered_at_ms"]) is int
         and first-plan["registered_at_ms"]>=20*60000
         and plan["anchors_ms"]==[first+i*STEP for i in range(4)]
         and len(seal["windows"])==4,
         "FROZEN_FUTURE_ANCHORS_CHANGED")
    for i,w in enumerate(seal["windows"]):
        need(w.get("index")==i and w.get("anchor_ms")==first+i*STEP
             and w.get("entry_first_ms")==w["anchor_ms"]+1000
             and w.get("entry_last_ms")==w["anchor_ms"]+4500
             and w.get("mature_at_ms")==[w["anchor_ms"]+h*60000 for h in HORIZONS],
             "SEALED_WINDOW_CLOCK_CHANGED")
        private_root(root/("window-%02d"%i))
    if (root/"worker-start.json").exists():
        worker,_=evidence(root,"worker-start.json")
        need(worker.get("schema")==RESEARCH+".worker.v1"
             and worker.get("sealed_sha256")==SEAL_PIN
             and worker.get("no_restart_no_retry") is True
             and worker.get("no_trading_or_model_promotion") is True
             and worker.get("broker_orders_sent")==0
             and plan["registered_at_ms"]<=worker.get("started_at_ms",0)<first,
             "WORKER_START_IDENTITY_CHANGED")
    return plan,seal

def exact_prefix_rows(receipts,period):
    need(type(receipts) is list and 1<=len(receipts)<=6,
         "SOURCE_PREFIX_RECEIPT_SET")
    quote_by_id={}
    event_by_time={}
    full_prefix_hashes=[]
    for record in receipts:
        need(type(record) is dict
             and type(record.get("path")) is str
             and type(record.get("prefix_sha256")) is str
             and len(record["prefix_sha256"])==64
             and type(record.get("complete_prefix_bytes")) is int,
             "PREFIX_RECEIPT_FIELDS_INVALID")
        path=Path(record["path"])
        need(path.is_absolute() and not path.is_symlink()
             and "segment-" in str(path.parent.name)
             and path.parent.parent.name=="input",
             "PREFIX_UNTRUSTED_PATH")
        bound=record["complete_prefix_bytes"]
        need(0<bound<=MAX_SOURCE_PREFIX,"SOURCE_PREFIX_SIZE")
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            st=os.fstat(fd)
            need(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
                and st.st_nlink==1 and not st.st_mode&0o077 and st.st_size>=bound,
                "SOURCE_PREFIX_REPLACED_OR_PUBLIC")
            pieces=[];cursor=0
            while cursor<bound:
                b=os.pread(fd,min(1024*1024,bound-cursor),cursor)
                need(bool(b),"SOURCE_PREFIX_SHORT")
                pieces.append(b);cursor+=len(b)
            data=b"".join(pieces)
            need(digest(data)==record["prefix_sha256"]
                 and data.endswith(b"\n"),
                 "ORIGINAL_CAPTURE_PREFIX_CHANGED")
        finally:os.close(fd)
        prior="0"*64
        header=None
        last=0
        ended=False
        for i,raw in enumerate(data.splitlines()):
            need(len(raw)<=65536,"ORIGINAL_RECORD_OVERLARGE")
            obj=strict(raw)
            need(obj.get("schema")=="heptatrader.laya-shadow-quotes.v1"
                 and obj.get("sequence")==i and obj.get("previous")==prior
                 and obj.get("sha256")==digest(canonical(
                     {k:v for k,v in obj.items() if k!="sha256"})),
                 "ORIGINAL_CHAIN_BROKEN")
            prior=obj["sha256"]
            at=obj.get("at_ms")
            need(type(at) is int and last<=at,"ORIGINAL_LOCAL_CLOCK_CHANGED")
            last=at
            kind=obj.get("kind")
            need(not ended,"ORIGINAL_EVENTS_AFTER_TERMINAL_END")
            if i==0:
                header=obj.get("data")
                need(kind=="HEADER" and header.get("instrument")=="EUR.USD"
                     and header.get("execution_domain")=="PAPER"
                     and header.get("period_sha256")==period
                     and header.get("source")=="IB_TOOL_GATEWAY"
                     and header.get("write_operations_allowed")==[]
                     and header.get("broker_orders_sent")==0,
                     "ORIGINAL_SOURCE_HEADER_CHANGED")
            elif kind=="QUOTE":
                q=obj["data"]
                event=q.get("observed_at_ms")
                expiry=q.get("stale_after_ms")
                need(q.get("source")=="IB" and q.get("authoritative") is True
                     and q.get("stale") is False
                     and type(event) is int and type(expiry) is int
                     and event<=at<=min(event+5000,expiry),
                     "ORIGINAL_IB_QUOTE_SCOPE_INVALID")
                bid=source_decimal(q.get("bid"));ask=source_decimal(q.get("ask"))
                need(bid<=ask,"ORIGINAL_QUOTE_CROSSED")
                quote={"source_id":obj["sha256"],"event_at_ms":event,
                       "available_at_ms":at,"valid_until_ms":min(event+5000,expiry),
                       "bid":bid,"ask":ask}
                quote_by_id[obj["sha256"]]=quote
                prev=event_by_time.get(event)
                if prev is not None:
                    need(prev["bid"]==bid and prev["ask"]==ask,
                         "CROSS_SOURCE_EVENT_PRICE_CONFLICT")
                    if prev["available_at_ms"]>at:
                        event_by_time[event]=quote
                else:event_by_time[event]=quote
            else:
                need(kind in ("FAILURE","END"),"UNKNOWN_ORIGINAL_EVENT")
                if kind=="END":
                    need(obj.get("data",{}).get("broker_orders_sent")==0,
                         "ORIGINAL_TERMINAL_SCOPE_INVALID")
                    ended=True
        full_prefix_hashes.append(record["prefix_sha256"])
    return quote_by_id,event_by_time,full_prefix_hashes

def frozen_feature_economics(root,cell,plan,seal,attempt,anchor):
    original,p_hash=evidence(cell,"preaction.json")
    need(attempt.get("preaction_sha256")==p_hash
         and original.get("schema")==RESEARCH+".preaction.v1"
         and original.get("anchor_ms")==anchor
         and original.get("intended_behavior")=="NO_TRADE_PRICE_ONLY"
         and original.get("no_model_prediction_or_propensity_derived") is True
         and original.get("same_original_period_sha256")==PERIOD_SHA
         and original.get("trade_authority") is False,
         "ORIGINAL_PREACTION_OR_SHA_CHANGED")
    recorded_at=attempt.get("attempt_recorded_at_ms")
    decision=original["decision_at_ms"]
    seal_at=attempt["original_preaction_durable_at_ms"]
    valid=original["input_quote_expiry_ms"]
    need(type(recorded_at) is int and type(decision) is int
         and anchor+1000<=decision<=anchor+4500
         and decision<=seal_at<=recorded_at
         and seal_at<=anchor+4500 and seal_at<valid
         and attempt.get("original_quote_expiry_ms")==valid,
         "PREACTION_SEAL_AFTER_SOURCE_EXPIRY")
    health=original.get("source_health")
    need(type(health) is dict and
         health.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
         and type(health.get("health_at_ms")) is int
         and 0<=decision-health["health_at_ms"]<=30000
         and type(health.get("usable_probes")) is int
         and health["usable_probes"]>=20
         and type(health.get("missing_probes")) is int
         and health["missing_probes"]<=4,
         "FROZEN_SOURCE_READINESS_NOT_ELIGIBLE")
    qmap,times,pins=exact_prefix_rows(original["source_prefix_receipts"],PERIOD_SHA)
    ids=original["input_source_ids"]
    need(type(ids) is list and len(ids)==13 and len(set(ids))==13,
         "ORIGINAL_THIRTEEN_QUOTE_IDENTITIES")
    samples=[]
    for i,source_id in enumerate(ids):
        need(source_id in qmap,"ORIGINAL_INPUT_ID_NOT_IN_FROZEN_SOURCE")
        q=qmap[source_id]
        endpoint=anchor-(12-i)*300000
        need(endpoint-5000<=q["event_at_ms"]<endpoint
             and q["available_at_ms"]<=endpoint,
             "PREGRID_CAUSAL_INPUT_QUOTE_MISSING")
        # The original feature routine did not check expiry at *every*
        # earlier grid; distinguish this from the producer's weaker pass.
        need(q["valid_until_ms"]>endpoint,
             "OLDER_INPUT_QUOTE_EXPIRED_AT_ITS_GRID")
        samples.append(q)
    latest=samples[-1]
    need(original["input_quote_event_at_ms"]==latest["event_at_ms"]
         and original["input_quote_available_at_ms"]==latest["available_at_ms"]
         and original["input_quote_expiry_ms"]==latest["valid_until_ms"]
         and source_decimal(original["input_quote_bid"])==latest["bid"]
         and source_decimal(original["input_quote_ask"])==latest["ask"]
         and latest["available_at_ms"]<=decision<latest["valid_until_ms"]
         and decision-latest["available_at_ms"]<=4500,
         "LAST_QUOTE_VALUES_OR_FRESHNESS_CHANGED")
    mids=[float((q["bid"]+q["ask"])/2) for q in samples]
    returns=[(b-a)/a for a,b in zip(mids,mids[1:])]
    mean=sum(returns)/len(returns)
    std=math.sqrt(sum((x-mean)**2 for x in returns)/len(returns))*10000
    hour=((anchor//1000-300)//3600)%24
    vec=[*( (mids[-1]/mids[-1-k]-1)*10000 for k in (1,3,6,12)),
         std,sum(abs(x) for x in returns)/len(returns)*10000,
         math.sin(2*math.pi*hour/24),math.cos(2*math.pi*hour/24)]
    actual=original.get("feature_vector")
    need(type(actual) is list and len(actual)==8
         and all(type(v) in (float,int) and math.isfinite(v) for v in actual)
         and max(abs(a-b) for a,b in zip(vec,actual))<=1e-8,
         "INDEPENDENT_FROZEN_FEATURE_RECOMPUTATION_FAILED")
    # Independently check the original fee/spread freeze; the producer's
    # preaction JSON must never supply a more favorable caller-defined cost.
    cost_raw=private_file(Path(plan["cost_file"]),2*1024*1024)
    cost=strict(cost_raw)
    need(digest(cost_raw)==plan["cost_sha256"]
         and cost.get("schema")=="heptatrader.ib-cost-evidence.offline.v1"
         and cost.get("period_source_sha256")==PERIOD_SHA
         and cost.get("trade_authority") is False
         and cost.get("actual_slippage_verified") is False
         and cost.get("fx_fee_missing_or_unproved_executions")==0,
         "ORIGINAL_COST_RECEIPT_CHANGED_OR_NOT_ATTESTED")
    spread=float(cost["quote_spread_full_bps"]["p99"])
    one_fee=float(cost["fx_commission_one_way_bps"]["p90"])
    live_spread=(float(latest["ask"]-latest["bid"]) /
                 float((latest["ask"]+latest["bid"])/2)*10000)
    must={
        "live_original_spread_bps":live_spread,
        "stressed_spread_bps":max(live_spread,spread),
        "roundtrip_commission_p90_bps":one_fee*2,
        "hypothetical_slippage_roundtrip_bps":4.0,
        "minimum_margin_bps":2.0,
        "required_absolute_prediction_bps":
            max(live_spread,spread)+2*one_fee+6.0,
    }
    observed=original.get("cost_stress")
    need(type(observed) is dict and set(observed)==set(must)
         and all(type(observed[k]) in (int,float)
                 and math.isfinite(observed[k]) and
                 abs(observed[k]-v)<=1e-7 for k,v in must.items()),
         "ORIGINAL_FROZEN_COST_WAS_CHANGED")
    return original,p_hash,pins

def audit_maturity(root,cell,plan,entry,preaction,pre_sha,w,horizon,now):
    name="maturity-%03d"%horizon
    due=w["anchor_ms"]+horizon*60000
    attempt_path=cell/(name+"-attempt.json")
    if not attempt_path.exists():
        return {"horizon_minutes":horizon,
                "state":"AWAITING_OR_MISSING_RECEIPT",
                "deadline_past":now>due+15000}
    result,_=evidence(cell,name+"-attempt.json")
    need(result.get("schema")==RESEARCH+".attempt.v1"
         and result.get("stage")==name
         and result.get("automatic_retry_allowed") is False
         and result.get("historical_price_or_future_quote_backfilled") is False
         and result.get("trade_authority") is False
         and result.get("broker_orders_sent")==0,
         "MATURITY_EVENT_SCOPE_CHANGED")
    state=result.get("state")
    if state=="CENSORED":
        need(result.get("reason_code") not in (None,"NONE")
             and not (cell/(name+".json")).exists(),
             "CENSORED_FUTURE_PRICE_WAS_FILLED_OR_REASON_MISSING")
        return {"horizon_minutes":horizon,"state":"CENSORED",
                "reason_code":result["reason_code"]}
    need(state=="MATURE_ORIGINAL_QUOTE_PRICE_ONLY" and preaction is not None,
         "FUTURE_PRICE_WITHOUT_CAUSAL_PREACTION")
    out,out_hash=evidence(cell,name+".json")
    need(out.get("schema")==RESEARCH+".mature-quote.v1"
         and result.get("quote_result_sha256")==out_hash
         and out.get("anchor_ms")==w["anchor_ms"]
         and out.get("horizon_minutes")==horizon
         and out.get("due_ms")==due
         and out.get("preaction_sha256")==pre_sha
         and out.get("selected_trading_action")=="NO_TRADE"
         and out.get("quote_side_prices_are_NOT_guaranteed_fills") is True
         and out.get("financing_and_realized_broker_net_pnl_not_attested") is True
         and out.get("model_training_or_promotion_qualified") is False
         and out.get("actual_broker_execution_pnl_samples")==0
         and out.get("trade_authority") is False,
         "ORIGINAL_FUTURE_OUTCOME_SCOPE_CHANGED")
    # The observable receipt time must also be bounded; later evidence cannot
    # be relabelled as if the entire operation completed before deadline.
    observed=result["attempt_recorded_at_ms"]
    need(due+6500<=observed<=due+15000,
         "MATURITY_RECEIPT_DURABLE_OUTSIDE_FROZEN_DEADLINE")
    qmap,events,pins=exact_prefix_rows(out["source_prefix_receipts"],PERIOD_SHA)
    match=out.get("first_original_mature_source_id")
    need(match in qmap,"FIRST_ORIGINAL_QUOTE_NOT_IN_PREFIX")
    q=qmap[match]
    need(due<=q["event_at_ms"]<=due+5000
         and due<=q["available_at_ms"]<=due+5000
         and q["available_at_ms"]<=observed,
         "FIRST_ORIGINAL_MATURE_QUOTE_LATE_OR_BACKFILLED")
    earliest=min((v for t,v in events.items()
                  if t>=due and v["available_at_ms"]<=observed),
                 key=lambda v:(v["event_at_ms"],v["available_at_ms"]),
                 default=None)
    need(earliest is not None and earliest["event_at_ms"]==q["event_at_ms"]
         and earliest["bid"]==q["bid"] and earliest["ask"]==q["ask"],
         "FUTURE_QUOTE_NOT_FIRST_ELIGIBLE")
    in_bid=source_decimal(preaction["input_quote_bid"])
    in_ask=source_decimal(preaction["input_quote_ask"])
    with localcontext() as ctx:
        ctx.prec=40
        mid=(in_bid+in_ask)/2
        final=(q["bid"]+q["ask"])/2
        check={
            "midpoint_move_bps":(final/mid-1)*10000,
            "hypothetical_long_entry_ask_exit_bid_move_bps":
                (q["bid"]-in_ask)/mid*10000,
            "hypothetical_short_entry_bid_exit_ask_move_bps":
                (in_bid-q["ask"])/mid*10000,
        }
    need(all(exact_decimal(out[k])==x for k,x in check.items()),
         "FUTURE_ORIGINAL_QUOTE_SIDE_ARITHMETIC_CHANGED")
    return {"horizon_minutes":horizon,"state":"VERIFIED_ORIGINAL_QUOTE_ONLY",
            "original_mature_quote_id":match,"source_prefix_count":len(pins),
            "source_prefix_receipts_sha256":digest(canonical(pins)),
            "quote_side_not_broker_fills":True}

def audit(*,root,frozen_code,observed_now=None):
    now=time.time_ns()//1000000 if observed_now is None else observed_now
    root=private_root(root)
    plan,seal=frozen_plan(root,frozen_code)
    windows=[]
    for w in seal["windows"]:
        cell=private_root(root/("window-%02d"%w["index"]))
        a=cell/"decision-attempt.json"
        anchor=w["anchor_ms"]
        if not a.exists():
            windows.append({"window":w["index"],"state":"AWAITING_OR_MISSING_DECISION",
                "deadline_past":now>anchor+4500})
            continue
        attempt,_=evidence(cell,"decision-attempt.json")
        need(attempt.get("schema")==RESEARCH+".attempt.v1"
             and attempt.get("stage")=="decision"
             and attempt.get("automatic_retry_allowed") is False
             and attempt.get("historical_price_or_future_quote_backfilled") is False
             and attempt.get("trade_authority") is False
             and attempt.get("model_promotion_allowed") is False
             and attempt.get("broker_orders_sent")==0,
             "DECISION_SCOPE_INVALID")
        need(anchor+1000<=attempt["attempt_recorded_at_ms"]<=anchor+4500 or
             (attempt.get("state")=="CENSORED"),
             "FROZEN_DECISION_DURABLE_AFTER_DEADLINE")
        preaction=None;pre_sha=None;source=[]
        if attempt.get("state")=="CENSORED":
            need(attempt.get("reason_code") not in (None,"NONE"),
                 "DECISION_CENSORED_WITHOUT_REASON")
            action="CENSORED"
        else:
            need(attempt.get("state")=="FROZEN_ORIGINAL_PRICE_ONLY"
                 and attempt.get("reason_code")=="NONE",
                 "UNEXPECTED_DECISION_STATE")
            preaction,pre_sha,source=frozen_feature_economics(
                root,cell,plan,seal,attempt,anchor)
            action="VERIFIED_FROZEN_ORIGINAL_PRICE_ONLY"
        maturities=[audit_maturity(root,cell,plan,attempt,preaction,pre_sha,
                                   w,h,now) for h in HORIZONS]
        windows.append({"window":w["index"],"state":action,
                        "source_prefix_count":len(source),
                        "maturities":maturities})
    return {
        "schema":SCHEMA,"audited_at_ms":now,
        "crosshost_precommit_hashes_checked":True,
        "precommitted_plan_sha256":PLAN_PIN,
        "precommitted_seal_sha256":SEAL_PIN,
        "frozen_runner_sha256":FROZEN_CODE_PIN,
        "window_count":len(windows),
        "windows":windows,
        "upstream_ib_market_quote_signed":False,
        "auditor_direct_broker_queries_or_orders":0,
        "native_product_owner_or_witness_enrolled":False,
        "live_trading_or_model_promotion_allowed":False,
        "no_model_predictions_or_execution_pnl_claim":True,
    }

def write_exclusive(path,report):
    output=Path(path)
    need(output.is_absolute() and not output.exists() and not output.is_symlink(),
         "NONEXCLUSIVE_OUTPUT")
    parent=output.parent
    parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    private_root(parent)
    data=json.dumps(report,sort_keys=True,indent=2,allow_nan=False).encode()+b"\n"
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    dr=os.open(parent,os.O_DIRECTORY|os.O_RDONLY)
    try:os.fsync(dr)
    finally:os.close(dr)

def main():
    p=argparse.ArgumentParser()
    for k in ("root","frozen-code","output"):
        p.add_argument("--"+k,required=True,type=Path)
    a=p.parse_args()
    report=audit(root=a.root,frozen_code=a.frozen_code)
    write_exclusive(a.output,report)
    print(json.dumps({"state":"INDEPENDENT_PRICE_RESEARCH_EVIDENCE_ONLY",
        "windows":[{"window":x["window"],"state":x["state"]} for x in report["windows"]],
        "orders_sent":0,"promotion_allowed":False},sort_keys=True))
if __name__=="__main__":
    main()
