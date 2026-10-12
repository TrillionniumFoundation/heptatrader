"""Independent, read-only review of a frozen finite FX Ridge research cohort.

No imports from the producing Train V2 modules. This audit never recomputes
future price from mutable hindsight data, sends broker requests, or edits a
campaign. It deliberately reports PENDING until the exact frozen evidence
exists; locally SHA-chained PAPER quotes are not broker-signed observations.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import time
from urllib.parse import quote as urlquote

SCHEMA = "heptatrader.fx-ridge-finite-independent-review.v1"
CAMPAIGN = "heptatrader.fx-ridge-finite-prospective-campaign.v1"
PLAN = "heptatrader.train-v2-event-plan.v1"
REG = "heptatrader.train-v2-local-prospective-registration.v1"
HORIZON = 900000
GAP = 1200000
MIN_LEAD = 30000

class InvalidProof(ValueError):
    pass

def require(value, reason):
    if not value:
        raise InvalidProof(reason)

def strict(raw):
    def pairs(value):
        result={}
        for key, item in value:
            require(key not in result, "DUPLICATE_JSON_FIELD")
            result[key]=item
        return result
    def const(_):
        raise InvalidProof("NONFINITE_JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=const)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def require_private_file(path, limit=1024*1024):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts and not path.is_symlink(),
            "UNSAFE_PRIVATE_PATH")
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
                and st.st_nlink==1 and st.st_mode&0o077==0
                and 0<st.st_size<=limit, "PRIVATE_FILE_MISSING_OR_UNSAFE")
        data=bytearray()
        while len(data)<st.st_size:
            chunk=os.read(fd,min(1024*1024,st.st_size-len(data)))
            require(bool(chunk),"TRUNCATED_PRIVATE_FILE")
            data.extend(chunk)
        after=os.fstat(fd)
        require((st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)==(
            after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),
            "PRIVATE_FILE_CHANGED")
        return bytes(data)
    finally:
        os.close(fd)

def file_sha(path, limit=1024*1024):
    return hashlib.sha256(require_private_file(path, limit)).hexdigest()

def read(path, limit=1024*1024):
    return strict(require_private_file(path,limit))

def private_dir(path):
    path=Path(path)
    require(path.is_absolute() and not path.is_symlink()
            and ".." not in path.parts,"PRIVATE_DIRECTORY_PATH")
    st=path.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid()
            and stat.S_IMODE(st.st_mode)==0o700,"PRIVATE_DIRECTORY_ACCESS")
    return path

def verify_seal(root):
    root=private_dir(root)
    intent=read(root/"campaign-intent.json")
    frozen=read(root/"campaign-sealed.json")
    require(intent.get("schema")==CAMPAIGN
            and frozen.get("schema")==CAMPAIGN+".sealed.v1"
            and frozen["intent_sha256"]==file_sha(root/"campaign-intent.json"),
            "INTENT_DIGEST_OR_SCHEMA")
    require(intent["fixed_count"]==4
            and intent["interval_ms"]==GAP
            and intent["outcome_wait_ms"]==6500
            and len(intent["anchors_ms"])==len(frozen["windows"])==4,
            "FROZEN_COHORT_SIZE")
    t0=intent["registered_operator_start_ms"]
    require(type(t0) is int and t0>0
            and frozen["sealed_at_ms"] < intent["anchors_ms"][0]
            and intent["anchors_ms"][0]>=t0+600000,
            "COHORT_REGISTRATION_TIME")
    require(all(type(t) is int and t%300000==0 for t in intent["anchors_ms"])
            and all(b-a==GAP for a,b in zip(intent["anchors_ms"],intent["anchors_ms"][1:])),
            "COHORT_GRID_OR_OVERLAP")
    for field in ("shadow_only","no_historical_test_reselection","no_prior_price_result_used_for_this_plan"):
        require(intent.get(field) is True, "HISTORY_RESELECTION_NOT_REFUSED")
    for field in ("trade_authority","model_promotion_allowed","automatic_training",
                  "trader_behavior_propensities_attested","canonical_owner_or_witness_enrolled",
                  "independent_broker_signed_source","production_model_qualification"):
        require(intent.get(field) is False,"COHORT_AUTHORITY_ESCALATION")
    for field in ("broker_orders_sent","broker_requests_sent"):
        require(intent.get(field)==0, "CAMPAIGN_UNAUTHORIZED_BROKER_MESSAGES")
    require(intent["original_validation_action"]=="NO_TRADE"
            and frozen["all_registered_before_first_decision"] is True
            and frozen["fixed_model_across_all_windows"] is True,
            "FROZEN_NO_TRADE_NOT_PRESERVED")
    config_path=Path(intent["config_path"]);cost_path=Path(intent["cost_path"])
    require(file_sha(config_path)==intent["config_sha256"]
            and file_sha(cost_path)==intent["cost_sha256"],
            "CONFIG_OR_COST_REWRITTEN")
    require(len({x["plan_id"] for x in frozen["windows"]})==4,
            "DUPLICATE_PLAN_IDS")
    core_hashes=set()
    specs=set()
    for index,item in enumerate(frozen["windows"]):
        require(item["index"]==index
                and item["anchor_ms"]==intent["anchors_ms"][index]
                and item["first_decision_ms"]==item["anchor_ms"]+1000
                and item["last_decision_ms"]==item["anchor_ms"]+4500
                and item["outcome_due_ms"]==item["anchor_ms"]+HORIZON,
                "CHANGED_DECISION_OR_OUTCOME_WINDOW")
        require(item["receipt_sealed_at_ms"]+MIN_LEAD<=item["first_decision_ms"]
                and item["receipt_sealed_at_ms"]<=frozen["sealed_at_ms"],
                "RETROACTIVE_REGISTRATION")
        cell=private_dir(root/("window-%02d"%index))
        names={"model_sha256":"model.json","manifest_sha256":"manifest.json",
               "plan_sha256":"plan.json","spec_sha256":"feature-spec.json",
               "registration_sha256":"local-registration.json"}
        require(all(file_sha(cell/v)==item[k] for k,v in names.items()),
                "FROZEN_WINDOW_BYTE_TAMPERING")
        model=read(cell/"model.json");manifest=read(cell/"manifest.json")
        plan=read(cell/"plan.json");spec=read(cell/"feature-spec.json")
        receipt=read(cell/"local-registration.json")
        require(plan["schema"]==PLAN
                and plan["plan_id"]==digest({k:v for k,v in plan.items() if k!="plan_id"})
                and plan["plan_id"]==item["plan_id"]
                and plan["feature_spec_id"]==digest(spec)
                and plan["period_sha256"]==intent["original_period_sha256"]
                and plan["first_decision_ms"]==item["first_decision_ms"]
                and plan["last_decision_ms"]==item["last_decision_ms"]
                and plan["minimum_mature_rows"]==16
                and plan["executor"]=="NONE_RESEARCH_REVIEW_ONLY",
                "FROZEN_PLAN_CHANGED")
        require(model["trained_rows"]==41243 and model["hyperparameter_ridge"]==50
                and model["historical_validation_selected_action"]=="NO_TRADE"
                and model["candidate_has_positive_validation_edge"] is False
                and model["trade_authority"] is False
                and model["promotion_allowed"] is False
                and file_sha(cell/"model.json")==intent["original_model_file_sha256"]
                and digest(model)==intent["historical_model_sha256"],
                "FROZEN_MODEL_OR_TRAINING_IDENTITY_CHANGED")
        require(manifest["anchor_ms"]==item["anchor_ms"]
                and manifest["outcome_due_ms"]==item["outcome_due_ms"]
                and manifest["historical_model_sha256"]==digest(model)
                and manifest["trade_authority"] is False
                and manifest["model_promotion_allowed"] is False
                and manifest["mode"]=="PRICE_ONLY_FROZEN_RIDGE_WITH_FROZEN_ABSTAIN",
                "FROZEN_MODEL_MANIFEST_CHANGED")
        for f in ("trade_authority","actor_update","promotion_allowed"):
            require(spec.get(f) is False, "SPEC_GRANTS_AUTHORITY")
        for f in ("trade_authority","actor_update","promotion_allowed","automatic_model_training"):
            require(plan.get(f) is False, "PLAN_GRANTS_AUTHORITY")
        require(receipt.get("schema")==REG
                and receipt.get("plan_id")==item["plan_id"]
                and receipt.get("plan")==plan
                and receipt.get("feature_spec")==spec
                and receipt.get("plan_sha256")==digest(plan)
                and receipt.get("sealed_at_ms")==item["receipt_sealed_at_ms"]
                and receipt.get("trade_authority") is False
                and receipt.get("independent_owner_signature") is False
                and receipt.get("broker_market_data_signed") is False,
                "REGISTRATION_IS_NOT_THE_FROZEN_PLAN")
        # Verify the actual registry seal and SQLite WAL row, not just a
        # same-directory self-asserted JSON receipt.
        regroot=private_dir(root/"registry")
        seal_path=regroot/"sealed-plans"/(item["plan_id"]+".json")
        require(read(seal_path)==receipt, "REGISTERED_SEAL_DIFFERS")
        with sqlite3.connect("file:"+urlquote(str(regroot/"registrations.sqlite"))+"?mode=ro",uri=True,timeout=5) as db:
            reg=db.execute("SELECT period_sha256,instrument,feature_spec_id,policy_id,"
                           "first_ms,last_ms,sealed_at_ms,receipt_sha256,registration_json "
                           "FROM registrations WHERE plan_id=?",(item["plan_id"],)).fetchone()
        require(reg is not None and reg[0]==plan["period_sha256"]
                and reg[1]=="EUR.USD"
                and reg[2]==digest(spec)
                and reg[3]==spec["behavior_policy_id"]
                and reg[4]==item["first_decision_ms"]
                and reg[5]==item["last_decision_ms"]
                and reg[6]==item["receipt_sealed_at_ms"]
                and reg[7]==file_sha(seal_path)
                and strict(reg[8].encode())==receipt,
                "SQLITE_REGISTRY_OR_SEAL_MISMATCH")
        core_hashes.add(item["model_sha256"])
        specs.add(item["spec_sha256"])
    require(len(core_hashes)==len(specs)==1,
            "INCONSISTENT_MODEL_OR_SPEC")
    return intent,frozen

def finite_float(x):
    if type(x) not in (int,float) or not math.isfinite(x):
        raise InvalidProof("NONFINITE_PRICE_OR_PREDICTION")
    return float(x)

def independent_price_prediction(decision,model,anchor):
    quotes=decision["history_quote_samples"]
    require(type(quotes) is list and len(quotes)==13,
            "ORIGINAL_CAUSAL_CLOSE_COUNT")
    close=[]
    for j,q in enumerate(quotes):
        end=anchor-(12-j)*300000
        require(type(q["event_at_ms"]) is int
                and type(q["available_at_ms"]) is int
                and end-5000<=q["event_at_ms"]<end
                and q["available_at_ms"]<=end,
                "NONCAUSAL_ORIGINAL_QUOTE_BUCKET")
        bid=float(q["bid"]);ask=float(q["ask"])
        require(0<bid<=ask and math.isfinite(ask),
                "INVALID_ORIGINAL_BID_ASK")
        close.append((bid+ask)/2)
    returns=[(close[i+1]-close[i])/close[i] for i in range(12)]
    hour=((anchor//1000-300)//3600)%24
    values=[*((close[-1]/close[-1-n]-1)*10000 for n in (1,3,6,12)),
            math.sqrt(sum((v-sum(returns)/12)**2 for v in returns)/12)*10000,
            sum(abs(v) for v in returns)/12*10000,
            math.sin(2*math.pi*hour/24),math.cos(2*math.pi*hour/24)]
    require(len(values)==8
            and all(abs(a-b)<1e-8 for a,b in zip(values,decision["feature_vector"])),
            "MODEL_FEATURE_REPRODUCTION_FAILED")
    means=model["mean"];sds=model["stddev"];weights=model["weights"]
    require(len(means)==len(sds)==8 and len(weights)==9,
            "MODEL_GEOMETRY_CHANGED")
    norm=[max(-5.,min(5.,(x-finite_float(mu))/finite_float(sd)))
          for x,mu,sd in zip(values,means,sds)]
    prediction=float(weights[0])+sum(float(w)*v for w,v in zip(weights[1:],norm))
    require(abs(prediction-float(decision["forecast_midpoint_bps"]))<1e-8,
            "PREDICTION_CANNOT_BE_RECOMPUTED")
    direction = "UP" if prediction > 0 else "DOWN" if prediction < 0 else "FLAT"
    require(decision.get("forecast_direction_research_only") == direction,
            "PREDICTION_DIRECTION_IDENTITY_CHANGED")
    return prediction

def independent_cost(decision,cost):
    q=decision["history_quote_samples"][-1]
    bid,ask=float(q["bid"]),float(q["ask"])
    live_spread=(ask-bid)/((bid+ask)/2)*10000
    cost_basis=cost["quote_spread_full_bps"]["p99"]
    commission=cost["fx_commission_one_way_bps"]["p90"]
    expected=max(live_spread,finite_float(cost_basis))+2*finite_float(commission)+6
    require(abs(expected-float(decision["cost_hurdle"]["required_absolute_prediction_bps"]))<1e-8,
            "HISTORICAL_COST_HURDLE_CHANGED")
    require(decision["forecast_magnitude_clears_cost"] is
            (abs(float(decision["forecast_midpoint_bps"]))>expected),
            "COST_CLEARS_FLAG_NOT_REPRODUCED")
    return expected

def verify_source_prefix(receipts,origin_root):
    require(type(receipts) is list and 1<=len(receipts)<=6,
            "SOURCE_PREFIX_COUNT")
    source=private_dir(Path(origin_root))
    for receipt in receipts:
        path=Path(receipt["path"])
        require(path.is_absolute() and not path.is_symlink()
                and path.parent.parent==source
                and path.name=="quotes.jsonl"
                and path.parent.name.startswith("segment-"),
                "SOURCE_PREFIX_SCOPE")
        size=receipt["complete_prefix_bytes"]
        require(type(size) is int and 0<size<=64*1024*1024,
                "SOURCE_PREFIX_SIZE")
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            st=os.fstat(fd)
            require(stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid()
                    and st.st_size>=size and st.st_mode&0o077==0,
                    "SOURCE_PREFIX_UNSAFE_OR_LOST")
            h=hashlib.sha256()
            offset=0
            while offset<size:
                data=os.pread(fd,min(1024*1024,size-offset),offset)
                require(data,"SOURCE_PREFIX_EARLY_EOF")
                h.update(data);offset+=len(data)
            require(h.hexdigest()==receipt["prefix_sha256"],
                    "ORIGINAL_QUOTE_PREFIX_CHANGED")
        finally:
            os.close(fd)

def stage_evidence(root, index, item, intent):
    cell=root/("window-%02d"%index)
    pred_receipt=cell/"decision-attempt.json"
    maturity_receipt=cell/"maturity-attempt.json"
    if not pred_receipt.exists():
        require(not maturity_receipt.exists(), "FUTURE_WITHOUT_PAST")
        return {"index":index, "decision_state":"PENDING",
                "maturity_state":"PENDING","prediction_recomputed":False}
    p=read(pred_receipt,8192)
    require(p["stage"]=="decision"
            and p["schema"]==CAMPAIGN+".attempt.v1"
            and p["trade_authority"] is False
            and p["model_promotion_allowed"] is False
            and p["broker_orders_sent"]==0
            and p["broker_requests_sent"]==0
            and p["nonretryable"] is True,
            "DECISION_ATTEMPT_IDENTITY")
    require(p["state"] in ("CENSORED","FROZEN_PRICE_ONLY"),
            "DECISION_ATTEMPT_UNKNOWN_STATE")
    decision=None
    if p["state"]=="FROZEN_PRICE_ONLY":
        require(p["reason_code"]=="NONE","DECISION_SUCCESS_WITH_REFUSAL")
        decision=read(cell/"preoutcome-decision.json",65536)
        q=read(cell/"qualification.json",8192)
        model=read(cell/"model.json")
        require(q["decision_sha256"]==file_sha(cell/"preoutcome-decision.json")
                and q["state"]=="PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE"
                and q["sealed_before_broker_reported_quote_expiry"] is True
                and q["seal_at_ms"] < q["last_original_quote_expiry_ms"]
                and q["seal_at_ms"] <= p["attempt_recorded_at_ms"],
                "QUOTE_EXPIRED_BEFORE_DURABLE_DECISION")
        require(item["first_decision_ms"]<=decision["decision_at_ms"]<=item["last_decision_ms"]
                and decision["decision_at_ms"]<=q["seal_at_ms"]
                and decision["plan_id"]==item["plan_id"]
                and decision["model_sha256"]==digest(model)
                and decision["selected_trading_action"]=="NO_TRADE"
                and decision["original_trader_behavior_propensity_attested"] is False
                and decision["native_owner_current"] is False
                and decision["trade_authority"] is False
                and decision["promotion_allowed"] is False,
                "ORIGINAL_DECISION_SCOPE_CHANGED")
        independent_price_prediction(decision,model,item["anchor_ms"])
        independent_cost(decision,read(Path(intent["cost_path"])))
        verify_source_prefix(decision["source_prefix_receipts"],
                             read(cell/"manifest.json")["source_root"])
    if not maturity_receipt.exists():
        return {"index":index, "decision_state":p["state"],
                "maturity_state":"PENDING",
                "prediction_recomputed":decision is not None}
    m=read(maturity_receipt,8192)
    require(m["stage"]=="maturity"
            and m["state"] in ("CENSORED","MATURE_PRICE_ONLY")
            and m["nonretryable"] is True
            and m["trade_authority"] is False
            and m["model_promotion_allowed"] is False
            and m["broker_orders_sent"]==0
            and m["broker_requests_sent"]==0
            and m["attempt_recorded_at_ms"]>=item["outcome_due_ms"]+6000,
            "MATURITY_ATTEMPT_INVALID")
    if m["state"]=="MATURE_PRICE_ONLY":
        require(decision is not None,"MATURE_WITHOUT_FROZEN_DECISION")
        outcome=read(cell/"mature-outcome.json",65536)
        require(outcome["predecision_sha256"]==file_sha(cell/"preoutcome-decision.json")
                and outcome["anchor_ms"]==item["anchor_ms"]
                and outcome["outcome_due_ms"]==item["outcome_due_ms"]
                and item["outcome_due_ms"]<=outcome["price_event_ms"]<=item["outcome_due_ms"]+5000
                and outcome["price_available_ms"]>=item["outcome_due_ms"]
                and outcome["input_quote_sha256"]==decision["history_quote_samples"][-1]["source_id"]
                and outcome["selected_trading_action"]=="NO_TRADE"
                and outcome["model_efficacy_proven"] is False
                and outcome["model_promotion_allowed"] is False
                and outcome["trade_authority"] is False
                and outcome["observed_trading_pnl_attested"] is False,
                "RESEARCH_OUTCOME_CLAIMS_MORE_THAN_PRICE")
        require(Decimal(outcome["forecast_midpoint_bps"])==Decimal(str(decision["forecast_midpoint_bps"]))
                and Decimal(outcome["forecast_error_bps"])==
                Decimal(outcome["forecast_midpoint_bps"])-Decimal(outcome["observed_original_midpoint_return_bps"]),
                "MATURITY_ERROR_ALGEBRA")
        verify_source_prefix(outcome["mature_source_prefix_receipts"],
                             read(cell/"manifest.json")["source_root"])
    return {"index":index, "decision_state":p["state"],
            "maturity_state":m["state"],
            "prediction_recomputed":decision is not None,
            "outcome_price_algebra_verified":m["state"]=="MATURE_PRICE_ONLY",
            "independent_broker_signed_outcome":False}

def audit(root):
    root=private_dir(root)
    intent,frozen=verify_seal(root)
    details=[stage_evidence(root,i,item,intent)
             for i,item in enumerate(frozen["windows"])]
    done=[v for v in details if v["maturity_state"]!="PENDING"]
    worker_path=root/"campaign-worker-start.json"
    if worker_path.exists():
        worker=read(worker_path)
        require(worker["schema"]==CAMPAIGN+".worker-start.v1"
                and worker["sealed_sha256"]==file_sha(root/"campaign-sealed.json")
                and worker["started_at_ms"]<frozen["windows"][0]["first_decision_ms"]
                and worker["no_order_api_or_model_promotion"] is True
                and worker["automatic_retries"] is False
                and worker["trade_authority"] is False,
                "STARTED_WORKER_NOT_PRECOMMITTED")
    else:
        require(not any(v["decision_state"]!="PENDING" for v in details),
                "DECISION_WITHOUT_FROZEN_WORKER_START")
    final_path=root/"campaign-final.json"
    if final_path.exists():
        final=read(final_path)
        require(final["schema"]==CAMPAIGN+".final.v1"
                and final["window_count"]==4
                and len(done)==4 and len(final["window_results"])==4
                and final["sealed_sha256"]==file_sha(root/"campaign-sealed.json")
                and final["intent_sha256"]==file_sha(root/"campaign-intent.json")
                and final["successful_matured_count"]==
                    sum(v["maturity_state"]=="MATURE_PRICE_ONLY" for v in details)
                and final["censored_count"]==
                    sum(v["maturity_state"]!="MATURE_PRICE_ONLY" for v in details)
                and final["automatic_promotion"] is False
                and final["model_economic_efficacy_proven"] is False
                and final["trade_authority"] is False
                and final["broker_orders_sent"]==0,
                "PREMATURE_OR_MUTATED_FINAL_CLAIM")
        for actual,record in zip(details,final["window_results"]):
            i=actual["index"]
            cell=root/("window-%02d"%i)
            require(actual["index"]==record["window"]
                    and record["plan_id"]==frozen["windows"][i]["plan_id"]
                    and actual["decision_state"]==record["decision_state"]
                    and actual["maturity_state"]==record["maturity_state"]
                    and record["decision_attempt_sha256"]==
                        file_sha(cell/"decision-attempt.json")
                    and record["maturity_attempt_sha256"]==
                        file_sha(cell/"maturity-attempt.json"),
                    "FINAL_NOT_MATCHING_FROZEN_EVENTS")
    return {
        "schema": SCHEMA,
        "checked_at_ms":time.time_ns()//1000000,
        "sealed_campaign_sha256":file_sha(root/"campaign-sealed.json"),
        "frozen_plan_count":4,
        "source_model_locked_and_registry_reconciled":True,
        "window_results":details,
        "pending_decisions":sum(v["decision_state"]=="PENDING" for v in details),
        "pending_maturities":sum(v["maturity_state"]=="PENDING" for v in details),
        "price_only_matured":sum(v["maturity_state"]=="MATURE_PRICE_ONLY" for v in details),
        "censored":sum(v["maturity_state"]=="CENSORED" for v in details),
        "all_four_windows_closed":len(done)==4,
        "broker_market_data_signed":False,
        "historical_ohlc_backfill_used":False,
        "independent_execution_pnl_attested":False,
        "statistically_qualified_alpha":False,
        "canonical_owner_or_witness_admitted":False,
        "model_promotion_allowed":False,
        "trade_authority":False,
        "broker_requests_by_verifier":0,
        "broker_orders_by_verifier":0,
        "research_hard_hold":True
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    result=audit(a.root)
    private_dir(a.output.parent)
    fd=os.open(a.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as f:
        f.write(json.dumps(result,sort_keys=True,indent=2)+"\n")
        f.flush();os.fsync(f.fileno())
    print(json.dumps({k:result[k] for k in (
        "frozen_plan_count","pending_decisions","pending_maturities",
        "price_only_matured","censored","all_four_windows_closed",
        "model_promotion_allowed","trade_authority")},sort_keys=True))
if __name__=="__main__":
    main()
