"""Independent read-only recheck of a frozen FX Ridge PAPER prospective episode.

No order, API connection, original episode mutation or model promotion. This
does not turn locally hashed IB quotes into broker signatures or an alpha claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
from decimal import Decimal

from historical_training.common import digest
from train_v2.causal_ttl import sealed_original_broker_expiry
from train_v2.adapters import make_spec

class AuditHold(ValueError):
    pass

def demand(condition, reason):
    if not condition:
        raise AuditHold(reason)

def raw(path, maximum=64*1024*1024):
    path=Path(path)
    demand(path.is_absolute() and not path.is_symlink(), "ABSOLUTE_REGULAR_PATH")
    fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        a=os.fstat(fd)
        demand(stat.S_ISREG(a.st_mode) and a.st_nlink==1 and a.st_uid==os.geteuid()
               and not a.st_mode&0o077 and 0<a.st_size<=maximum,
               "UNSAFE_OR_OVERSIZED_PRIVATE_SOURCE")
        remaining=a.st_size
        pieces=[]
        while remaining:
            part=os.read(fd,min(1024*1024,remaining))
            demand(bool(part),"SHORT_READ")
            pieces.append(part)
            remaining-=len(part)
        b=os.fstat(fd)
        demand((a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns)==
               (b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns),
               "SOURCE_MUTATED_DURING_READ")
        return b"".join(pieces)
    finally:
        os.close(fd)

def read(path, max_size=65536):
    return json.loads(raw(path,max_size))

def hash_bytes(b):
    return hashlib.sha256(b).hexdigest()

def original_prefix(receipt):
    demand(type(receipt) is dict and
           set(("path","complete_prefix_bytes","prefix_sha256"))<=set(receipt),
           "ORIGINAL_PREFIX_RECEIPT_INVALID")
    path=Path(receipt["path"])
    size=receipt["complete_prefix_bytes"]
    demand(path.is_absolute() and not path.is_symlink() and
           type(size) is int and 0<size<=64*1024*1024,
           "ORIGINAL_PREFIX_PATH_INVALID")
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        demand(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
               and st.st_nlink==1 and not st.st_mode&0o077 and st.st_size>=size,
               "ORIGINAL_PREFIX_NOT_PRIVATE_OR_SHORT")
        b=bytearray()
        while len(b)<size:
            chunk=os.pread(fd,min(1024*1024,size-len(b)),len(b))
            demand(bool(chunk),"ORIGINAL_PREFIX_INCOMPLETE")
            b.extend(chunk)
        demand(hash_bytes(b)==receipt["prefix_sha256"],
               "ORIGINAL_PREFIX_HASH_CHANGED")
        return size
    finally:
        os.close(fd)

def compute_features(samples,anchor_ms):
    demand(len(samples)==13 and
           len(set(x["source_id"] for x in samples))==13,
           "INPUT_OR_DUPLICATE_COUNT")
    closes=[]
    for k,row in enumerate(samples):
        end=anchor_ms-(12-k)*300000
        demand(row["source_kind"]=="LIVE_CAUSAL_QUOTE" and
               end-5000<=row["event_at_ms"]<end and
               row["available_at_ms"]<=end and
               row["available_at_ms"]>=row["event_at_ms"] and
               row["valid_until_ms"]>=end,
               "NONCAUSAL_ORIGINAL_BAR_CLOSE")
        bid=Decimal(row["bid"]);ask=Decimal(row["ask"])
        demand(bid.is_finite() and ask.is_finite() and 0<bid<=ask,
               "CROSSED_ORIGINAL_QUOTE")
        closes.append(float((bid+ask)/2))
    lags=[(closes[-1]/closes[-1-k]-1)*10000 for k in (1,3,6,12)]
    rets=[(b-a)/a for a,b in zip(closes[:-1],closes[1:])]
    avg=sum(rets)/len(rets)
    sd=math.sqrt(sum((r-avg)**2 for r in rets)/len(rets))
    hour=((anchor_ms//1000-300)//3600)%24
    return lags+[sd*10000,sum(abs(r) for r in rets)/len(rets)*10000,
                 math.sin(2*math.pi*hour/24),math.cos(2*math.pi*hour/24)]

def independent_prediction(model,feature):
    mean=model["mean"];sd=model["stddev"];w=model["weights"]
    demand(len(mean)==len(sd)==len(feature)==8 and len(w)==9
           and all(type(x) in (int,float) and math.isfinite(x) for x in
                   mean+sd+w+feature) and all(x>0 for x in sd),
           "FROZEN_WEIGHT_GEOMETRY_INVALID")
    normalized=[max(-5.,min(5.,(x-m)/s)) for x,m,s in zip(feature,mean,sd)]
    return w[0]+sum(a*b for a,b in zip(normalized,w[1:]))

def check_preaction(model,manifest,plan,receipt,decision,qualification,at_ms,cost_receipt):
    demand(model["schema"]=="heptatrader.fx-ridge-historical-model.v1"
           and model["trade_authority"] is False
           and model["candidate_has_positive_validation_edge"] is False
           and model["historical_holdout_previously_seen"] is True
           and model["historical_validation_selected_action"]=="NO_TRADE",
           "ORIGINAL_HISTORICAL_NONADMISSION_CHANGED")
    demand(manifest["schema"]=="heptatrader.fx-ridge-original-research.v1"
           and manifest["historical_model_sha256"]==digest(model)
           and manifest["validation_trading_edge_qualified"] is False
           and manifest["trading_behavior_propensity_known"] is False
           and manifest["external_production_root_enrolled"] is False
           and manifest["broker_signed_quote"] is False,
           "PRETENDED_PRODUCT_TRUST")
    demand(plan["plan_id"]==receipt["plan_id"]==decision["plan_id"]
           and receipt["plan"]==plan and receipt["plan_sha256"]==digest(plan)
           and plan["registered_at_ms"]<=receipt["sealed_at_ms"]
           and receipt["first_decision_ms"]==plan["first_decision_ms"]
           and receipt["last_decision_ms"]==plan["last_decision_ms"]
           and receipt["original_source_root"]==manifest["source_root"]
           and receipt["broker_market_data_signed"] is False
           and receipt["independent_owner_signature"] is False
           and receipt["can_relabel_previously_inspected_data_as_unseen"] is False
           and plan["period_sha256"]==model["period_sha256"]==
                   manifest["original_period_sha256"]
           and plan["trade_authority"] is False
           and plan["promotion_allowed"] is False
           and receipt["trade_authority"] is False
           and receipt["canonical_agentd_owner_enrolled"] is False
           and receipt["sealed_at_ms"]<=plan["first_decision_ms"]-30000,
           "REGISTRATION_NOT_FROZEN_BEFORE_WINDOW")
    demand(plan["first_decision_ms"]<=decision["decision_at_ms"]<=plan["last_decision_ms"]
           and manifest["anchor_ms"]<decision["decision_at_ms"]
           and decision["anchor_ms"]==manifest["anchor_ms"]
           and decision["outcome_mature_not_before_ms"]==manifest["outcome_due_ms"]
           and manifest["outcome_due_ms"]==manifest["anchor_ms"]+900000,
           "PROSPECTIVE_CLOCK_INVALID")
    demand(decision["selected_trading_action"]=="NO_TRADE"
           and decision["trade_authority"] is False
           and decision["promotion_allowed"] is False
           and decision["actual_execution_reward"] is False
           and qualification["trade_authority"] is False
           and qualification["model_promotion_allowed"] is False
           and qualification["state"]=="PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE"
           and qualification["sealed_before_broker_reported_quote_expiry"] is True,
           "UNAUTHORIZED_TRADING_OR_HINDSIGHT")
    demand(qualification["seal_at_ms"]<qualification["last_original_quote_expiry_ms"]
           and qualification["seal_at_ms"]<=at_ms
           and decision["original_trader_behavior_propensity_attested"] is False
           and decision["native_owner_current"] is False,
           "QUALIFICATION_FALSIFIED")
    feat=compute_features(decision["history_quote_samples"],manifest["anchor_ms"])
    demand(len(decision["feature_vector"])==len(feat) and
           all(math.isclose(float(a),b,rel_tol=0,abs_tol=1e-8) for a,b in zip(decision["feature_vector"],feat)),
           "FROZEN_FEATURES_CHANGED")
    pred=independent_prediction(model,feat)
    demand(math.isclose(pred,float(decision["forecast_midpoint_bps"]),rel_tol=0,abs_tol=1e-9),
           "FROZEN_RIDGE_FORECAST_CHANGED")
    cost=decision["cost_hurdle"]
    demand(type(cost) is dict
           and all(type(cost[k]) in (float,int) and math.isfinite(cost[k]) for k in
                   ("live_original_spread_bps","stressed_spread_bps",
                    "roundtrip_commission_p90_bps","hypothetical_slippage_roundtrip_bps",
                    "minimum_margin_bps","required_absolute_prediction_bps")),
           "COST_HURDLE_INVALID")
    bid=Decimal(decision["history_quote_samples"][-1]["bid"])
    ask=Decimal(decision["history_quote_samples"][-1]["ask"])
    spread=float((ask-bid)/((ask+bid)/2)*10000)
    frozen_spread=max(spread, float(cost_receipt["quote_spread_full_bps"]["p99"]))
    roundtrip_fee=2*float(cost_receipt["fx_commission_one_way_bps"]["p90"])
    hurdle=frozen_spread+roundtrip_fee+4.0+2.0
    demand(math.isclose(spread,cost["live_original_spread_bps"],abs_tol=1e-8)
           and math.isclose(frozen_spread,cost["stressed_spread_bps"],abs_tol=1e-8)
           and math.isclose(roundtrip_fee,cost["roundtrip_commission_p90_bps"],abs_tol=1e-8)
           and math.isclose(cost["hypothetical_slippage_roundtrip_bps"],4.,abs_tol=1e-8)
           and math.isclose(cost["minimum_margin_bps"],2.,abs_tol=1e-8)
           and math.isclose(hurdle,cost["required_absolute_prediction_bps"],abs_tol=1e-8)
           and abs(pred)<=hurdle and
           decision["forecast_magnitude_clears_cost"] is False,
           "COST_ABSTENTION_NOT_REPRODUCIBLE")
    return {"independent_forecast_bps":pred,"calibrated_required_bps":hurdle,
            "decision_at_ms":decision["decision_at_ms"],"outcome_due_ms":manifest["outcome_due_ms"]}

def check_maturity(root,model,manifest,decision,at_ms):
    outcome_path=root/"mature-outcome.json"
    if not outcome_path.exists():
        return {"state":"PREOUTCOME_ONLY_HOLD_NOT_MATURE_RESULT"}
    outcome=read(outcome_path,200000)
    due=decision["outcome_mature_not_before_ms"]
    demand(at_ms>=due+6000 and outcome["schema"]=="heptatrader.fx-ridge-original-mature-price.v1"
           and outcome["outcome_due_ms"]==due
           and outcome["selected_trading_action"]=="NO_TRADE"
           and outcome["observed_trading_pnl_attested"] is False
           and outcome["model_efficacy_proven"] is False
           and outcome["trade_authority"] is False,
           "MATURE_RESULT_NOT_ADMISSIBLE")
    demand(outcome["input_quote_sha256"]==decision["history_quote_samples"][-1]["source_id"]
           and math.isclose(float(outcome["forecast_midpoint_bps"]),
                            float(decision["forecast_midpoint_bps"]),abs_tol=1e-12)
           and due<=outcome["price_event_ms"]<=due+5000
           and due<=outcome["price_available_ms"]<=at_ms,
           "FUTURE_LABEL_OR_INPUT_BINDING_FAILED")
    # Verify *mature* prefixes and independently choose the earliest actual
    # source event at the pre-registered horizon; never optimize the label.
    spec=make_spec(manifest["original_period_sha256"],manifest["policy_id"],
                   horizon_ms=900000,lookback=13,broker_reported_expiry=True)
    original_root=Path(manifest["source_root"])
    receipts=outcome["mature_source_prefix_receipts"]
    demand(type(receipts) is list and 1<=len(receipts)<=6,
           "MATURE_SOURCE_RECEIPT_SET_INVALID")
    unique={}
    for receipt in receipts:
        path=Path(receipt["path"])
        demand(path.parent.parent==original_root and not path.parent.is_symlink(),
               "MATURE_SOURCE_NOT_ORIGINAL_ROOT")
        original_prefix(receipt)
        rows,proof=sealed_original_broker_expiry(path,spec)
        demand(proof["original_period_verified"] is True
               and proof["historical_revisions_used"] is False
               and proof["broker_requests_sent"]==0
               and proof["broker_orders_sent"]==0,
               "NONORIGINAL_MATURE_QUOTES")
        for quote in rows:
            if quote["available_at_ms"]>at_ms:
                continue
            t=quote["event_at_ms"]
            if t in unique:
                demand(unique[t]["bid"]==quote["bid"] and
                       unique[t]["ask"]==quote["ask"],
                       "CONFLICTING_MATURE_TIMESTAMP")
            else:
                unique[t]=quote
    eligible=[q for q in unique.values() if due<=q["event_at_ms"]<=due+5000
              and due<=q["available_at_ms"]<=at_ms]
    demand(bool(eligible),"NO_INDEPENDENT_FUTURE_QUOTE")
    selected=min(eligible,key=lambda q:(q["event_at_ms"],q["available_at_ms"]))
    demand(selected["source_id"]==outcome["outcome_quote_sha256"]
           and selected["event_at_ms"]==outcome["price_event_ms"]
           and selected["available_at_ms"]==outcome["price_available_ms"],
           "MATURITY_CHERRY_PICK_OR_WRONG_QUOTE")
    origin=decision["history_quote_samples"][-1]
    first=(Decimal(origin["bid"])+Decimal(origin["ask"]))/2
    future=(Decimal(selected["bid"])+Decimal(selected["ask"]))/2
    movement=(future/first-1)*10000
    prediction=Decimal(str(decision["forecast_midpoint_bps"]))
    demand(abs(movement-Decimal(outcome["observed_original_midpoint_return_bps"]))<=Decimal("1e-18")
           and abs((prediction-movement)-Decimal(outcome["forecast_error_bps"]))<=Decimal("1e-18")
           and outcome["research_direction_correct"]==(
               (prediction>0 and movement>0) or (prediction<0 and movement<0)),
           "INDEPENDENT_PRICE_OR_ERROR_DIFFERS")
    return {"state":"MATURE_ORIGINAL_PRICE_INDEPENDENTLY_RECOMPUTED",
            "observed_midpoint_return_bps":str(movement),
            "forecast_error_bps":str(prediction-movement),
            "direction_correct_research_only":outcome["research_direction_correct"],
            "outcome_quote_sha256":selected["source_id"],
            "original_quote_records_verified":len(unique)}

def audit(root,at_ms,cost_path,period_path):
    root=Path(root)
    model=read(root/"model.json",1024*1024)
    manifest=read(root/"manifest.json")
    cost=read(cost_path,1024*1024)
    period=read(period_path,1024*1024)
    plan=read(root/"plan.json")
    reg=read(root/"local-registration.json")
    decision=read(root/"preoutcome-decision.json")
    q=read(root/"qualification.json")
    demand(hash_bytes(raw(cost_path))==manifest["cost_receipt_sha256"]==model["cost_receipt_sha256"]
           and hash_bytes(raw(period_path))==manifest["original_period_sha256"]==model["period_sha256"]
           and period["execution_domain"]=="PAPER"
           and cost["actual_slippage_verified"] is False
           and cost["fx_fee_missing_or_unproved_executions"]==0,
           "PINNED_ACCOUNT_OR_COST_SOURCE_CHANGED")
    demand(hash_bytes(raw(root/"model.json"))==decision["model_file_sha256"]
           and hash_bytes(raw(root/"manifest.json"))==decision["manifest_sha256"]
           and hash_bytes(raw(root/"preoutcome-decision.json"))==q["decision_sha256"]
           and decision["model_sha256"]==digest(model),
           "FROZEN_ARTIFACT_BYTES_CHANGED")
    checks=check_preaction(model,manifest,plan,reg,decision,q,at_ms,cost)
    prefix_bytes=0
    original_root=Path(manifest["source_root"])
    for r in decision["source_prefix_receipts"]:
        p=Path(r["path"])
        demand(p.parent.parent==original_root and not p.parent.is_symlink(),
               "PREACTION_SOURCE_NOT_ORIGINAL_ROOT")
        prefix_bytes+=original_prefix(r)
    outcome=check_maturity(root,model,manifest,decision,at_ms)
    return {"schema":"heptatrader.fx-ridge-independent-original-check.v1",
            "checked_at_ms":at_ms,"research_only":True,
            "candidate_has_positive_validation_edge":False,
            "source_locally_hashed_not_broker_signed":True,
            "broker_queries_sent":0,"broker_orders_sent":0,
            "model_promotion_allowed":False,"native_ledger_witness_admitted":False,
            "preaction":checks,"source_prefix_count":len(decision["source_prefix_receipts"]),
            "verified_original_source_prefix_bytes":prefix_bytes,
            "outcome":outcome}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True,type=Path)
    ap.add_argument("--cost",required=True,type=Path)
    ap.add_argument("--period",required=True,type=Path)
    ap.add_argument("--output",required=True,type=Path)
    args=ap.parse_args()
    report=audit(args.root,time.time_ns()//1000000,args.cost,args.period)
    args.output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    data=(json.dumps(report,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(args.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    print(json.dumps({"state":report["outcome"]["state"],
                      "forecast_bps":report["preaction"]["independent_forecast_bps"],
                      "original_prefixes":report["source_prefix_count"],
                      "broker_orders_sent":0}))
if __name__=="__main__":main()
