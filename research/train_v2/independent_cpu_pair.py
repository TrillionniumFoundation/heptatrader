"""Independent single-segment verifier for already frozen CPU Native PRICE forecasts.

Runs on a separate host using only Python standard library and transferred
original bytes; neither source callbacks nor Pocket4's outcome evaluator are
imported. No IB connection, order API, execution, training or promotion.
"""
from __future__ import annotations
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path

SCHEMA="heptatrader.native-price-ticks-physical-single-segment-review.v1"
COST=Decimal("10.9133557")
MAX_BYTES=5*1024*1024

class IndependentError(ValueError):
    pass

def require(ok,why):
    if not ok:raise IndependentError(why)

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def unique_pairs(items):
    result={}
    for key,val in items:
        require(key not in result,"DUPLICATE_JSON_KEY")
        result[key]=val
    return result

def strict_json(raw):
    return json.loads(raw,object_pairs_hook=unique_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(IndependentError("NONFINITE_JSON")))

def read_bytes(path,max_bytes=MAX_BYTES):
    p=Path(path)
    require(p.is_file() and not p.is_symlink(),"SOURCE_FILE_UNSAFE")
    require(0<p.stat().st_size<=max_bytes,"SOURCE_FILE_SIZE")
    return p.read_bytes()

def doc(path,max_bytes=1_000_000):
    return strict_json(read_bytes(path,max_bytes))

def price(value):
    require(not isinstance(value,bool) and value is not None,"PRICE_MISSING")
    try:r=Decimal(str(value))
    except (ValueError,TypeError,InvalidOperation) as exc:
        raise IndependentError("PRICE_INVALID") from exc
    require(r.is_finite() and 0<r<1000000,"PRICE_NONFINITE")
    return r

def _timestamp(x):
    require(type(x)is int and x>0,"TIMESTAMP_INVALID")
    return x

def original_source(path,expected_sha256):
    raw=read_bytes(path)
    require(sha(raw)==expected_sha256 and raw.endswith(b"\n"),
            "ORIGINAL_SOURCE_SHA_OR_TAIL")
    previous="0"*64
    at_last=0
    quotes=[]
    header=None
    ended=False
    rows=raw.splitlines()
    require(8<=len(rows)<=25000,"ORIGINAL_SOURCE_COUNT")
    for seq,line in enumerate(rows):
        require(len(line)<=65536,"ORIGINAL_LINE_TOO_LONG")
        r=strict_json(line)
        require(r.get("schema")=="heptatrader.laya-shadow-quotes.v1"
                and r.get("sequence")==seq
                and r.get("previous")==previous
                and r.get("sha256")==digest({k:v for k,v in r.items()
                                               if k!="sha256"}),
                "ORIGINAL_SOURCE_CHAIN_DIVERGED")
        at=_timestamp(r.get("at_ms"))
        require(at>=at_last and not ended,"ORIGINAL_CLOCK_OR_AFTER_END")
        at_last=at;previous=r["sha256"]
        kind=r.get("kind")
        if seq==0:
            header=r.get("data")
            require(kind=="HEADER" and isinstance(header,dict)
                    and header.get("execution_domain")=="PAPER"
                    and header.get("instrument")=="EUR.USD"
                    and header.get("source")=="IB_TOOL_GATEWAY"
                    and header.get("write_operations_allowed")==[]
                    and header.get("broker_orders_sent")==0,
                    "ORIGINAL_HEADER_SCOPE_INVALID")
        elif kind=="QUOTE":
            q=r.get("data")
            require(isinstance(q,dict) and q.get("instrument")=="EUR.USD"
                    and q.get("source")=="IB"
                    and q.get("authoritative") is True
                    and q.get("stale") is False,"ORIGINAL_QUOTE_AUTHORITY")
            event=_timestamp(q.get("observed_at_ms"))
            expiry=_timestamp(q.get("stale_after_ms"))
            require(event<=at<=min(event+5000,expiry),
                    "ORIGINAL_QUOTE_EXPIRED_AT_OBSERVATION")
            b,a=price(q.get("bid")),price(q.get("ask"))
            require(b<=a,"ORIGINAL_QUOTE_CROSSED")
            quotes.append({"event":event,"known":at,"expiry":expiry,
                           "receipt":r["sha256"],"mid":(b+a)/2})
        elif kind=="END":
            require(r.get("data",{}).get("broker_orders_sent")==0,
                    "ORIGINAL_END_ORDER_SCOPE")
            ended=True
        else:
            require(kind=="FAILURE","ORIGINAL_EVENT_KIND_UNKNOWN")
    require(ended and rows and strict_json(rows[-1]).get("kind")=="END",
            "ORIGINAL_SEGMENT_NOT_SEALED")
    require(10<=len(quotes)<=20000,"ORIGINAL_NO_QUOTES")
    ids=[q["receipt"] for q in quotes]
    require(len(ids)==len(set(ids)),"ORIGINAL_DUPLICATE_RECEIPT")
    return {"header":header,"quotes":quotes,"source_sha256":sha(raw),
            "chain_head":previous,"quote_count":len(quotes),
            "last_at_ms":at_last}

def load_slot(path,quotes,period):
    root=Path(path)
    files={n:read_bytes(root/(n+".json"),200000) for n in
           ("plan","input","prediction","commit","pocket4-outcome")}
    p,i,pred,c,out={n:strict_json(raw) for n,raw in files.items()}.values()
    require(p.get("schema")=="heptatrader.native-price-ticks-future-plan.v1"
            and i.get("schema")=="heptatrader.native-price-ticks-future-input.v1"
            and pred.get("schema")=="heptatrader.native-price-ticks-future-prediction.v1"
            and c.get("schema")=="heptatrader.native-price-ticks-future-commit.v1"
            and out.get("schema")=="heptatrader.native-price-ticks-sealed-outcome.v1",
            "FROZEN_DOCUMENT_SCHEMA")
    plan_id=p.get("plan_id")
    require(plan_id==digest({k:v for k,v in p.items() if k!="plan_id"}),
            "FROZEN_PLAN_HASH_CHANGED")
    require(all(x.get("plan_id")==plan_id for x in (i,pred,c,out)),
            "CROSS_PLAN_IDENTITY")
    require(p.get("original_period_sha256")==period
            and p.get("forecast_profile")==
              "FUTURE_NATIVE_NUMERIC_ROUND_HALF_EVEN_1E8_TICKS_V1"
            and p.get("original_source_profile")==
              "ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2"
            and p.get("no_trade_policy")=="NO_ACTION_NO_BROKER_ORDER"
            and p.get("target_horizon_sec")==300
            and p.get("trade_authority") is False
            and p.get("automatic_model_promotion") is False
            and p.get("canonical_agentd_owner_enrolled") is False,
            "FROZEN_PLAN_UNAUTHORIZED_PROFILE")
    require(pred.get("source_input_sha256")==sha(files["input"])
            and c.get("input_sha256")==sha(files["input"])
            and c.get("prediction_sha256")==sha(files["prediction"])
            and c.get("prediction_sealed_before_deadline") is True
            and c.get("future_fixed_score_profile")==p["forecast_profile"],
            "FROZEN_PREDICTION_LINK_BROKEN")
    known=i.get("quote_known_at_ms")
    event=i.get("quote_event_at_ms")
    asof=i.get("source_asof_ms")
    decision=i.get("feature_decision_ms")
    sealed=c.get("post_fsync_at_ms")
    due=_timestamp(event)+300000
    require(_timestamp(p.get("registered_at_ms"))+30000<=
             _timestamp(p.get("slot_start_ms")) <= event
            and event>=p["baseline_trained_until_ms"]
            and p["slot_start_ms"]<=asof<=p["window_end_ms"]
            and event<=known<=asof<=decision
            <=pred.get("prediction_ready_at_ms")<=sealed
            <=p["window_end_ms"]<due,
            "FROZEN_PREDICTION_NOT_TIMELY")
    receipt=i.get("quote_receipt")
    original=[q for q in quotes if q["receipt"]==receipt]
    require(len(original)==1,"ORIGINAL_REFERENCE_RECEIPT_MISSING")
    q=original[0]
    require(q["event"]==event and q["known"]==known,
            "ORIGINAL_REFERENCE_TIMESTAMP_CHANGED")
    require(event<=decision<=min(event+5000,q["expiry"]),
            "REFERENCE_EXPIRED_AT_DECISION")
    tick=pred.get("future_fixed_score",{}).get("scaled_bps_ticks")
    require(type(tick)is int and abs(tick)<100000000000000,
            "FROZEN_SCORE_TICKS_INVALID")
    score=Decimal(tick)/Decimal(100000000)
    require(Decimal(str(pred["future_fixed_score"]["rounded_bps"]))==score
            and pred["future_fixed_score"]["profile"]==p["forecast_profile"]
            and pred["future_fixed_score"]["source_model_sha256"]==
                p["baseline_model_sha256"]
            and pred["baseline_model_sha256"]==p["baseline_model_sha256"],
            "FROZEN_SCORE_IDENTITY_CHANGED")
    ordered=sorted(quotes,key=lambda row:(row["event"],row["known"]))
    endpoints=[x for x in ordered if x["event"]>=due]
    require(bool(endpoints),"ORIGINAL_OUTCOME_NOT_SEALED")
    endpoint=endpoints[0]
    require(endpoint["event"]<=due+5000 and endpoint["known"]<=
            _timestamp(strict_json(read_bytes(root/"pocket4-outcome.json"))["original_price_due_at_ms"])+600000,
            "ORIGINAL_ENDPOINT_NOT_CAUSAL")
    path=[x for x in ordered if event<=x["event"]<=endpoint["event"]]
    require(path and path[0]["event"]==event,"PRICE_PATH_REFERENCE_MISSING")
    require(all(b["event"]-a["event"]<=30000
                for a,b in zip(path,path[1:])),
            "ORIGINAL_PRICE_PATH_GAP")
    realized=(endpoint["mid"]/q["mid"]-1)*Decimal(10000)
    direction=((score>0)==(realized>0)) if score and realized else None
    require(out.get("audit_id")==digest({k:v for k,v in out.items()
                                          if k!="audit_id"})
            and out.get("state")=="OBSERVED_ORIGINAL_PAPER_MID_PRICE_ONLY"
            and out.get("source_quote_receipt")==receipt
            and out.get("source_event_at_ms")==event
            and out.get("original_quote_known_at_ms")==known
            and out.get("prediction_fsynced_at_ms")==sealed
            and out.get("original_price_due_at_ms")==due
            and out.get("frozen_model_sha256")==p["baseline_model_sha256"]
            and out.get("model_score_fixed_bps")==str(score)
            and Decimal(str(out.get("original_price_change_bps")))==realized
            and out.get("price_direction_match") is direction
            and out.get("absolute_price_move_clears_prior_hypothetical_cost") is
                (abs(realized)>=COST)
            and out.get("original_source_sealed_price_observed") is True
            and out.get("original_price_only_not_executable_fill") is True
            and out.get("actual_executed_net_pnl_samples")==0
            and out.get("model_promotion_permitted") is False
            and out.get("broker_orders_sent")==0,
            "ORIGINAL_OUTCOME_NOT_INDEPENDENTLY_REPRODUCED")
    return {"plan_id":plan_id,"frozen_model_sha256":p["baseline_model_sha256"],
            "source_quote_receipt":receipt,"event_at_ms":event,
            "decision_at_ms":decision,"prediction_fsynced_at_ms":sealed,
            "outcome_due_at_ms":due,"outcome_observed_quote_receipt":endpoint["receipt"],
            "endpoint_delay_ms":endpoint["event"]-due,
            "forecast_mid_bps":str(score),"observed_mid_bps":str(realized),
            "direction_match":direction,
            "absolute_mid_move_above_frozen_hypothetical_cost":abs(realized)>=COST,
            "pocket4_outcome_audit_id":out["audit_id"],
            "pocket4_outcome_file_sha256":sha(files["pocket4-outcome"]),
            "original_plan_sha256":sha(files["plan"]),
            "original_input_sha256":sha(files["input"]),
            "original_prediction_sha256":sha(files["prediction"]),
            "original_commit_sha256":sha(files["commit"]),
            "broker_orders_sent":0}

def audit_pair(root, expected_source_sha256):
    root=Path(root)
    require(root.is_dir() and not root.is_symlink() and
            len(expected_source_sha256)==64,"CROSSHOST_ROOT_OR_PIN")
    original=original_source(root/"original/quotes.jsonl",
                             expected_source_sha256)
    parts=[load_slot(root/("slot-"+n),original["quotes"],
                     original["header"]["period_sha256"])
           for n in ("01","02")]
    a,b=parts
    require(a["plan_id"]!=b["plan_id"]
            and a["source_quote_receipt"]!=b["source_quote_receipt"]
            and a["frozen_model_sha256"]==b["frozen_model_sha256"]
            and a["event_at_ms"]+300000<b["event_at_ms"]
            and a["outcome_due_at_ms"]<b["event_at_ms"],
            "CROSSHOST_TWO_PRICE_HORIZONS_NOT_INDEPENDENT")
    saved=doc(root/"pocket4-pair-closeout.json")
    require(saved.get("audit_id")==digest({k:v for k,v in saved.items()
                                           if k!="audit_id"})
            and saved.get("schema")==
              "heptatrader.native-price-ticks-pair-original-closeout.v1"
            and saved.get("state")==
              "TWO_SEALED_PRICE_OUTCOMES_ONLY_NO_ALPHA"
            and saved.get("forecast_count")==2
            and saved.get("mature_sealed_price_outcomes")==2
            and saved.get("censored_price_outcomes")==0
            and saved.get("awaiting_natural_seal_or_endpoint")==0
            and saved.get("observed_price_direction_matches")==
               sum(p["direction_match"] is True for p in parts)
            and saved.get("observed_price_direction_mismatches")==
               sum(p["direction_match"] is False for p in parts)
            and saved.get("observed_price_moves_below_prior_assumed_cost")==
               sum(p["absolute_mid_move_above_frozen_hypothetical_cost"] is False
                   for p in parts)
            and Decimal(str(saved.get("prior_assumed_cost_plus_margin_bps")))==COST
            and saved.get("actual_net_pnl_trade_samples")==0
            and saved.get("statistical_alpha_qualified") is False
            and saved.get("model_promotion_permitted") is False
            and saved.get("broker_orders_sent")==0,
            "CROSSHOST_POCKET4_PAIR_REPORT_NOT_REPRODUCED")
    per=saved.get("per_forecast")
    require(isinstance(per,list) and len(per)==2,
            "CROSSHOST_PAIR_DETAIL_COUNT")
    for recomputed,old in zip(parts,per):
        require(old.get("plan_id")==recomputed["plan_id"]
                and old.get("score_id") in (
                  doc(root/("slot-01" if recomputed is a else
                             "slot-02")/"prediction.json")
                  ["future_fixed_score"]["score_id"],)
                and Decimal(str(old.get("forecast_bps")))==
                    Decimal(recomputed["forecast_mid_bps"])
                and Decimal(str(old.get("sealed_price_change_bps")))==
                    Decimal(recomputed["observed_mid_bps"])
                and old.get("direction_match") is recomputed["direction_match"]
                and old.get("original_outcome_audit_id")==
                    recomputed["pocket4_outcome_audit_id"],
                "CROSSHOST_PER_FORECAST_ORIGINAL_RESULT_CHANGED")
    report={
        "schema":SCHEMA,
        "mode":"PHYSICAL_SEPARATE_HOST_SOURCE_BYTES_INDEPENDENT_PRICE_MATH",
        "auditor_code_sha256":sha(Path(__file__).read_bytes()),
        "original_pocket4_quote_bytes_sha256":original["source_sha256"],
        "original_quote_segment_complete_and_hash_chain_verified":True,
        "original_quote_count":original["quote_count"],
        "original_segment_chain_head":original["chain_head"],
        "retained_source_segment_only_not_full_archive":True,
        "pocket4_prior_outcome_cohort_sha256":
            sha(read_bytes(root/"pocket4-pair-closeout.json")),
        "original_period_sha256":original["header"]["period_sha256"],
        "forecast_count":2,
        "independently_recomputed_original_price_outcomes":2,
        "direction_matches":sum(p["direction_match"] is True for p in parts),
        "direction_mismatches":sum(p["direction_match"] is False for p in parts),
        "absolute_price_moves_above_frozen_hypothetical_cost":
            sum(p["absolute_mid_move_above_frozen_hypothetical_cost"] for p in parts),
        "assumed_cost_plus_safety_margin_bps":str(COST),
        "per_forecast":parts,
        "true_trader_behavior_propensity_attested":False,
        "native_model_independently_executed_on_second_host":False,
        "independent_canonical_observer_key_enrolled":False,
        "full_original_archive_independently_copied":False,
        "actual_broker_fills":0,
        "executed_net_pnl_samples":0,
        "net_economic_alpha_qualified":False,
        "production_training_or_actor_update":False,
        "automatic_model_selection":False,
        "broker_queries_sent":0,
        "broker_orders_sent":0,
        "trade_authority":False,
    }
    return {**report,"audit_id":digest(report)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--expected-original-sha256",required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    report=audit_pair(args.root,args.expected_original_sha256)
    dest=args.output
    require(not dest.exists() and not dest.is_symlink(),
            "CROSSHOST_IMMUTABLE_OUTPUT_REQUIRED")
    dest.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    body=(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
    fd=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600)
    with os.fdopen(fd,"wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    directory=os.open(dest.parent,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
    try:os.fsync(directory)
    finally:os.close(directory)
    print(json.dumps({
        "state":"TWO_ORIGINAL_PRICES_INDEPENDENTLY_RECOMPUTED",
        "direction_matches":report["direction_matches"],
        "direction_mismatches":report["direction_mismatches"],
        "observations":report["independently_recomputed_original_price_outcomes"],
        "actual_executed_net_pnl_samples":0,
        "broker_orders_sent":0,
        "audit_sha256":sha(body)},sort_keys=True))

if __name__=="__main__":
    main()
