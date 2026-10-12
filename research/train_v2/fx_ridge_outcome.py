"""After-horizon, original-hash-chain price-only result for a frozen Ridge FX shadow.

Read-only. No broker queries, price substitution, order, training or promotion.
Missing quotes or changed source prefixes are censored, never fabricated.
"""
from __future__ import annotations
import argparse, hashlib, json, os, time
from decimal import Decimal
from pathlib import Path
from .fx_ridge_original import (
    HORIZON_MS, newest_source_chain, recover_frozen, require, raw_sha,
    ResearchHold
)
from .original_policy_audit import write_once
from .registry import verify_registration

def verify_original_prefixes(receipts):
    require(type(receipts) is list and 1<=len(receipts)<=6,
            "FROZEN_PREFIX_SET_CHANGED")
    for r in receipts:
        path=Path(r["path"])
        size=r["complete_prefix_bytes"]
        require(path.is_absolute() and not path.is_symlink()
                and type(size) is int and 0<size<=64*1024*1024,
                "INVALID_FROZEN_PREFIX_PATH")
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            st=os.fstat(fd)
            require(st.st_size>=size and st.st_uid==os.geteuid()
                    and not st.st_mode&0o077,"ORIGINAL_PREFIX_REPLACED_OR_TRUNCATED")
            data=b""
            while len(data)<size:
                chunk=os.pread(fd,min(1024*1024,size-len(data)),len(data))
                require(bool(chunk),"ORIGINAL_PREFIX_SHORT_READ")
                data+=chunk
            require(hashlib.sha256(data).hexdigest()==r["prefix_sha256"],
                    "ORIGINAL_PREFIX_CONTENT_CHANGED")
        finally:os.close(fd)

def verify_price_maturity(decision,now,rows):
    anchor=decision["anchor_ms"]
    due=decision["outcome_mature_not_before_ms"]
    require(due==anchor+HORIZON_MS
            and now>=due+6000,"ORIGINAL_OUTCOME_NOT_YET_MATURE")
    require(decision["selected_trading_action"]=="NO_TRADE"
            and decision["actual_execution_reward"] is False,
            "RESEARCH_ACTION_OR_REWARD_CHANGED")
    origin=decision["history_quote_samples"][-1]
    require(origin["event_at_ms"]<anchor and origin["available_at_ms"]<=anchor,
            "ORIGINAL_ANCHOR_NOT_CAUSAL")
    # Always first original observation at or after scheduled future target.
    # Do not choose the most favorable event or backfill with historical OHLC.
    accepted=[x for x in rows if due<=x["event_at_ms"]<=due+5000
              and x["available_at_ms"]>=due and x["available_at_ms"]<=now]
    require(bool(accepted),"NO_ORIGINAL_MATURE_SOURCE_IN_FROZEN_WINDOW")
    result=min(accepted,key=lambda x:(x["event_at_ms"],x["available_at_ms"]))
    entry=(Decimal(origin["bid"])+Decimal(origin["ask"]))/2
    exit_=(Decimal(result["bid"])+Decimal(result["ask"]))/2
    move=(exit_/entry-1)*10000
    prediction=Decimal(str(decision["forecast_midpoint_bps"]))
    return {"schema":"heptatrader.fx-ridge-original-mature-price.v1",
            "anchor_ms":anchor,"outcome_due_ms":due,
            "price_event_ms":result["event_at_ms"],
            "price_available_ms":result["available_at_ms"],
            "input_quote_sha256":origin["source_id"],
            "outcome_quote_sha256":result["source_id"],
            "forecast_midpoint_bps":str(prediction),
            "observed_original_midpoint_return_bps":str(move),
            "forecast_error_bps":str(prediction-move),
            "research_direction_correct":(prediction>0 and move>0) or
                                           (prediction<0 and move<0),
            "selected_trading_action":"NO_TRADE",
            "hypothetical_no_trade_pnl_usd":"0",
            "observed_trading_pnl_attested":False,
            "original_source_locally_hashed_not_broker_signed":True,
            "native_product_owner_enrolled":False,
            "model_efficacy_proven":False,
            "model_promotion_allowed":False,
            "trade_authority":False,"broker_requests_sent":0,"broker_orders_sent":0}

def mature(*,config_path,cost_path,registry_root,root):
    config,period_sha,cost,model,manifest,plan,spec,registration=(
        recover_frozen(root,config_path,cost_path))
    root=Path(root)
    decision=__import__("historical_training.common",fromlist=["read"]).read(
        root/"preoutcome-decision.json",65536)
    qualification=__import__("historical_training.common",fromlist=["read"]).read(
        root/"qualification.json",65536)
    require(qualification["decision_sha256"]==raw_sha(root/"preoutcome-decision.json")
            and qualification["state"]=="PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE"
            and qualification["sealed_before_broker_reported_quote_expiry"] is True
            and decision["plan_id"]==plan["plan_id"]
            and decision["model_sha256"]==manifest["historical_model_sha256"],
            "RESEARCH_PREOUTCOME_PROOF_CHANGED")
    now=time.time_ns()//1000000
    require(now>=manifest["outcome_due_ms"]+6000,
            "OUTCOME_BEFORE_ORIGINAL_MATURITY")
    verify_original_prefixes(decision["source_prefix_receipts"])
    rows,source_proofs=newest_source_chain(config,spec,now)
    verifier_receipt=verify_registration(registry_root,plan,spec,
                                         Path(source_proofs[-1]["path"]))
    require(verifier_receipt==registration,
            "FROZEN_REGISTRATION_CHANGED")
    output=verify_price_maturity(decision,now,rows)
    output["predecision_sha256"]=raw_sha(root/"preoutcome-decision.json")
    output["mature_source_prefix_receipts"]=source_proofs
    write_once(root/"mature-outcome.json",output)
    return {"state":"PRICE_ONLY_REAL_ORIGINAL_OUTCOME_MATURED_NOT_MODEL_EFFICACY",
            "observed_original_midpoint_return_bps":
                output["observed_original_midpoint_return_bps"],
            "trade_authority":False,"broker_orders_sent":0}

def main():
    p=argparse.ArgumentParser()
    for k in ("config","cost","registry-root","root"):
        p.add_argument("--"+k,required=True,type=Path)
    a=p.parse_args()
    os.umask(0o077)
    r=mature(config_path=a.config,cost_path=a.cost,
             registry_root=a.registry_root,root=a.root)
    print(json.dumps(r,sort_keys=True),flush=True)
if __name__=="__main__":main()
