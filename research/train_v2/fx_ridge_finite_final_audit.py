"""Independently reconcile a finished, immutable four-window FX Ridge PAPER campaign.

No live broker connections, order changes, training, model promotion or hindsight
rescore. The source-prefix and numeric replays must be separately produced by
the independent original-quote checker before this summary can be sealed.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

SCHEMA = "heptatrader.fx-ridge-finite-independent-final-audit.v1"

class AuditHold(ValueError):
    pass

def need(ok, reason):
    if not ok:
        raise AuditHold(reason)

def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()

def source_file(path, limit=4*1024*1024):
    path=Path(path)
    need(path.is_absolute() and not path.is_symlink(), "SOURCE_PATH_SCOPE")
    fd=os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid()
             and s.st_nlink==1 and not s.st_mode&0o077
             and 0<s.st_size<=limit, "SOURCE_NOT_PRIVATE")
        chunks=[]
        remain=s.st_size
        while remain:
            data=os.read(fd,min(remain,1024*1024))
            need(bool(data),"SOURCE_SHORT_READ")
            chunks.append(data);remain-=len(data)
        last=os.fstat(fd)
        need((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==
             (last.st_dev,last.st_ino,last.st_size,last.st_mtime_ns),
             "SOURCE_CHANGED_ON_READ")
        return b"".join(chunks)
    finally:
        os.close(fd)

def object_pairs(pairs):
    result={}
    for k,v in pairs:
        need(k not in result,"DUPLICATE_JSON_KEY")
        result[k]=v
    return result

def read(path,limit=4*1024*1024):
    def nonfinite(x):
        raise AuditHold("NONFINITE_INPUT")
    obj=json.loads(source_file(path,limit),object_pairs_hook=object_pairs,
                   parse_constant=nonfinite)
    need(type(obj) is dict,"OBJECT_REQUIRED")
    return obj

def hashed(path):
    return sha_bytes(source_file(path))

def code_sha(path):
    # Git-managed source is normally mode 0644. Private evidence stays 0600.
    path=Path(path)
    need(path.is_absolute() and not path.is_symlink(), "CODE_PATH_INVALID")
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid()
             and 0<s.st_size<=1024*1024, "CODE_SOURCE_INVALID")
        data=os.read(fd,s.st_size+1)
        last=os.fstat(fd)
        need(len(data)==s.st_size and
             (s.st_ino,s.st_dev,s.st_size,s.st_mtime_ns)==
             (last.st_ino,last.st_dev,last.st_size,last.st_mtime_ns),
             "CODE_CHANGED_DURING_READ")
        return sha_bytes(data)
    finally:
        os.close(fd)

def number(value):
    try:
        value=Decimal(str(value))
        need(value.is_finite(),"NONFINITE_NUMBER")
        return value
    except (ValueError,ArithmeticError) as err:
        raise AuditHold("BAD_NUMBER") from err

def positive_interval(times):
    return all(type(x) is int and x>0 for x in times) and all(
        b>a for a,b in zip(times,times[1:]))

def verify(root,independent, *, at_ms=None):
    root,independent=Path(root),Path(independent)
    for directory in (root,independent):
        need(directory.is_absolute() and not directory.is_symlink(),
             "AUDIT_DIRECTORY_INVALID")
        st=directory.stat()
        need(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid()
             and stat.S_IMODE(st.st_mode)==0o700,
             "AUDIT_DIRECTORY_NOT_PRIVATE")
    at_ms=time.time_ns()//1_000_000 if at_ms is None else at_ms
    intent=read(root/"campaign-intent.json")
    sealed=read(root/"campaign-sealed.json")
    worker=read(root/"campaign-worker-start.json")
    final=read(root/"campaign-final.json")
    need(intent["schema"]=="heptatrader.fx-ridge-finite-prospective-campaign.v1"
         and sealed["schema"]==intent["schema"]+".sealed.v1"
         and final["schema"]==intent["schema"]+".final.v1",
         "CAMPAIGN_SCHEMAS_MISMATCH")
    need(intent["fixed_count"]==4 and intent["interval_ms"]==1200000
         and intent["shadow_only"] is True
         and intent["trade_authority"] is False
         and intent["model_promotion_allowed"] is False
         and intent["automatic_training"] is False
         and intent["no_historical_test_reselection"] is True
         and intent["independent_broker_signed_source"] is False
         and intent["trader_behavior_propensities_attested"] is False
         and intent["canonical_owner_or_witness_enrolled"] is False,
         "INVALID_RESEARCH_ONLY_SCOPE")
    anchors=intent["anchors_ms"]
    need(len(anchors)==4 and positive_interval(anchors)
         and all(b-a==intent["interval_ms"] for a,b in zip(anchors,anchors[1:]))
         and all(t%300000==0 for t in anchors),
         "ALTERED_OR_OVERLAPPING_WINDOWS")
    need(len(sealed["windows"])==len(final["window_results"])==4
         and sealed["intent_sha256"]==final["intent_sha256"]
         ==hashed(root/"campaign-intent.json")
         and final["sealed_sha256"]==worker["sealed_sha256"]
         ==hashed(root/"campaign-sealed.json")
         and sealed["all_registered_before_first_decision"] is True
         and sealed["fixed_model_across_all_windows"] is True
         and worker["automatic_retries"] is False
         and worker["no_order_api_or_model_promotion"] is True
         and sealed["sealed_at_ms"]<=worker["started_at_ms"]
         < sealed["windows"][0]["first_decision_ms"]
         and final["completed_at_ms"]>=anchors[-1]+900000+6000
         and final["completed_at_ms"]<=at_ms
         and final["automatic_promotion"] is False
         and final["model_economic_efficacy_proven"] is False
         and final["broker_orders_sent"]==0,
         "CAMPAIGN_TIMELINE_OR_SEALS_INVALID")
    need(hashed(Path(intent["original_model_root"])/"model.json")
         ==intent["original_model_file_sha256"]
         and hashed(Path(intent["config_path"]))==intent["config_sha256"]
         and hashed(Path(intent["cost_path"]))==intent["cost_sha256"]
         and intent["original_model_file_sha256"]==
             sealed["windows"][0]["model_sha256"],
         "FROZEN_INPUT_CHANGED")
    code=Path(__file__).parent/"fx_ridge_finite_campaign.py"
    need(code_sha(code)==intent["campaign_source_code_sha256"],
         "CAMPAIGN_EXECUTION_CODE_CHANGED")
    from_module=Path(__file__).parent/"fx_ridge_original.py"
    need(code_sha(from_module)==intent["original_source_code_sha256"],
         "FROZEN_MODEL_EXECUTION_CODE_CHANGED")
    states=[]
    seen_plans=set()
    for pos,(s,f) in enumerate(zip(sealed["windows"],final["window_results"])):
        cell=root/("window-%02d"%pos)
        st=cell.stat()
        need(st.st_uid==os.geteuid() and stat.S_IMODE(st.st_mode)==0o700
             and s["index"]==f["window"]==pos
             and s["anchor_ms"]==anchors[pos]
             and s["plan_id"]==f["plan_id"]
             and s["plan_id"] not in seen_plans,
             "WINDOW_IDENTITY_MISMATCH")
        seen_plans.add(s["plan_id"])
        need(s["receipt_sealed_at_ms"]+30000<=s["first_decision_ms"]
             and s["first_decision_ms"]==s["anchor_ms"]+1000
             and s["last_decision_ms"]==s["anchor_ms"]+4500
             and s["outcome_due_ms"]==s["anchor_ms"]+900000,
             "PROSPECTIVE_WINDOW_BOUNDARY_CHANGED")
        for key,filename in (
            ("registration_sha256","local-registration.json"),
            ("model_sha256","model.json"),
            ("manifest_sha256","manifest.json"),
            ("plan_sha256","plan.json"),
            ("spec_sha256","feature-spec.json")):
            need(s[key]==hashed(cell/filename),"REGISTERED_FILE_CHANGED:"+filename)
        plan=read(cell/"plan.json")
        receipt=read(cell/"local-registration.json")
        manifest=read(cell/"manifest.json")
        need(plan["plan_id"]==receipt["plan_id"]==s["plan_id"]
             and plan["registered_at_ms"]<=receipt["sealed_at_ms"]
             and receipt["sealed_at_ms"]==s["receipt_sealed_at_ms"]
             and receipt["trade_authority"] is False
             and receipt["broker_market_data_signed"] is False
             and receipt["canonical_agentd_owner_enrolled"] is False
             and receipt["independent_owner_signature"] is False
             and plan["trade_authority"] is False
             and manifest["anchor_ms"]==s["anchor_ms"]
             and manifest["outcome_due_ms"]==s["outcome_due_ms"]
             and manifest["validation_trading_edge_qualified"] is False,
             "ORIGINAL_RESEARCH_REGISTRATION_UNQUALIFIED")
        d=read(cell/"decision-attempt.json")
        m=read(cell/"maturity-attempt.json")
        need(f["decision_attempt_sha256"]==hashed(cell/"decision-attempt.json")
             and f["maturity_attempt_sha256"]==hashed(cell/"maturity-attempt.json")
             and f["decision_state"]==d["state"]
             and f["maturity_state"]==m["state"]
             and d["stage"]=="decision" and m["stage"]=="maturity"
             and d["nonretryable"] is True and m["nonretryable"] is True
             and d["trade_authority"] is False and m["trade_authority"] is False
             and d["broker_orders_sent"]==m["broker_orders_sent"]==0
             and d["model_promotion_allowed"] is False
             and m["model_promotion_allowed"] is False,
             "ATTEMPT_HASH_OR_SCOPE_CHANGED")
        if d["state"]=="CENSORED":
            need(m["state"]=="CENSORED"
                 and not (cell/"mature-outcome.json").exists()
                 and not (independent/("independent-window-%02d.json"%pos)).exists(),
                 "CENSORED_WINDOW_WAS_BACKFILLED")
            states.append({
                "window":pos,"decision":"CENSORED","maturity":"CENSORED",
                "reason_code":d["reason_code"],
                "price_observation_eligible":False,
                "trade_authority":False,
            })
            continue
        need(d["state"]=="FROZEN_PRICE_ONLY"
             and m["state"]=="MATURE_PRICE_ONLY",
             "UNEXPECTED_SUCCESS_STATES")
        decision=read(cell/"preoutcome-decision.json",100000)
        qualification=read(cell/"qualification.json")
        outcome=read(cell/"mature-outcome.json",200000)
        independently=read(independent/("independent-window-%02d.json"%pos))
        need(qualification["decision_sha256"]==outcome["predecision_sha256"]
             ==hashed(cell/"preoutcome-decision.json")
             and qualification["state"]=="PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE"
             and qualification["sealed_before_broker_reported_quote_expiry"] is True
             and decision["plan_id"]==plan["plan_id"]
             and decision["model_sha256"]==manifest["historical_model_sha256"]
             and qualification["seal_at_ms"]<qualification["last_original_quote_expiry_ms"]
             and qualification["seal_at_ms"]<=d["attempt_recorded_at_ms"]
             <=s["last_decision_ms"]
             and s["first_decision_ms"]<=decision["decision_at_ms"]
             <=qualification["seal_at_ms"]<=s["last_decision_ms"]
             and outcome["outcome_due_ms"]==s["outcome_due_ms"]
             and s["outcome_due_ms"]<=outcome["price_event_ms"]
             <=s["outcome_due_ms"]+5000
             and outcome["price_event_ms"]<=outcome["price_available_ms"]
             <=m["attempt_recorded_at_ms"]<=final["completed_at_ms"]
             and m["attempt_recorded_at_ms"]>=s["outcome_due_ms"]+6000,
             "PREACTION_OR_MATURITY_TIMELINE_CHANGED")
        need(decision["selected_trading_action"]==
             qualification["selected_trading_action"]==
             outcome["selected_trading_action"]=="NO_TRADE"
             and decision["forecast_magnitude_clears_cost"] is False
             and decision["actual_execution_reward"] is False
             and decision["native_owner_current"] is False
             and outcome["model_efficacy_proven"] is False
             and outcome["observed_trading_pnl_attested"] is False
             and outcome["trade_authority"] is False
             and independently["research_only"] is True
             and independently["model_promotion_allowed"] is False
             and independently["native_ledger_witness_admitted"] is False
             and independently["source_locally_hashed_not_broker_signed"] is True
             and independently["broker_orders_sent"]==0
             and independently["outcome"]["state"]==
                 "MATURE_ORIGINAL_PRICE_INDEPENDENTLY_RECOMPUTED",
             "RESEARCH_ONLY_NONTRADING_CLAIM_CHANGED")
        pred=number(decision["forecast_midpoint_bps"])
        observed=number(outcome["observed_original_midpoint_return_bps"])
        error=number(outcome["forecast_error_bps"])
        need(abs(pred-number(independently["preaction"]["independent_forecast_bps"]))
             <Decimal("0.00000001")
             and pred-observed==error
             and number(independently["outcome"]["observed_midpoint_return_bps"])==observed
             and number(independently["outcome"]["forecast_error_bps"])==error
             and independently["outcome"]["outcome_quote_sha256"]==
                 outcome["outcome_quote_sha256"]
             and independently["outcome"]["direction_correct_research_only"]
                 is outcome["research_direction_correct"]
             and abs(pred)<
                 number(independently["preaction"]["calibrated_required_bps"]),
             "INDEPENDENT_ECONOMIC_OR_NUMERIC_REPLAY_DISAGREES")
        states.append({
            "window":pos,"decision":"FROZEN_PRICE_ONLY",
            "maturity":"MATURE_PRICE_ONLY",
            "forecast_bps":str(pred),
            "observed_price_change_bps":str(observed),
            "forecast_error_bps":str(error),
            "absolute_error_bps":str(abs(error)),
            "direction_correct":outcome["research_direction_correct"],
            "required_abs_forecast_bps":str(
                number(independently["preaction"]["calibrated_required_bps"])),
            "decision_sha256":hashed(cell/"preoutcome-decision.json"),
            "outcome_sha256":hashed(cell/"mature-outcome.json"),
            "independent_check_sha256":hashed(independent/(
                "independent-window-%02d.json"%pos)),
            "trade_authority":False,
        })
    valid=[s for s in states if s["maturity"]=="MATURE_PRICE_ONLY"]
    censor=[s for s in states if s["maturity"]=="CENSORED"]
    need(len(valid)==final["successful_matured_count"]
         and len(censor)==final["censored_count"]
         and len(valid)+len(censor)==4,
         "FINAL_COUNTS_OR_DENOMINATORS_CHANGED")
    with localcontext() as ctx:
        ctx.prec=50
        mae=sum((number(r["absolute_error_bps"]) for r in valid),
                Decimal(0))/len(valid) if valid else None
    correct=sum(r["direction_correct"] is True for r in valid)
    return {
        "schema": SCHEMA, "checked_at_utc":
            datetime.fromtimestamp(at_ms/1000,timezone.utc).isoformat(),
        "campaign_intent_sha256":hashed(root/"campaign-intent.json"),
        "campaign_seal_sha256":hashed(root/"campaign-sealed.json"),
        "campaign_final_sha256":hashed(root/"campaign-final.json"),
        "campaign_source_sha256":intent["campaign_source_code_sha256"],
        "fixed_model_sha256":intent["historical_model_sha256"],
        "original_period_sha256":intent["original_period_sha256"],
        "total_preregistered_windows":4,
        "eligible_price_only_predictions":len(valid),
        "censored_windows":len(censor),
        "direction_correct_count":correct,
        "directional_accuracy_conditional_on_eligible_price_only":
            str(Decimal(correct)/Decimal(len(valid))) if valid else None,
        "mean_abs_forecast_error_bps":str(mae) if mae is not None else None,
        "predictions_covering_frozen_cost_hurdle":0,
        "executed_trade_pnl_samples":0,
        "live_trading_alpha_proven":False,
        "broker_queries_sent_by_audit":0,
        "broker_orders_sent_by_audit":0,
        "independent_physical_broker_signed_source_proven":False,
        "trusted_crosshost_production_witness_proven":False,
        "canonical_owner_current_or_promotion_allowed":False,
        "automatic_training":False,
        "trade_authority":False,
        "windows":states
    }

def write_exclusive(path, report):
    os.umask(0o077)
    path=Path(path)
    need(path.is_absolute() and path.parent.is_dir()
         and not path.parent.is_symlink(), "OUTPUT_PATH_NOT_PRIVATE")
    st=path.parent.stat()
    need(st.st_uid==os.geteuid() and stat.S_IMODE(st.st_mode)==0o700,
         "OUTPUT_DIRECTORY_PERMISSIONS")
    raw=(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        dirfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(dirfd)
        finally:os.close(dirfd)
    except BaseException:
        path.unlink(missing_ok=True)
        raise

def main():
    ap=argparse.ArgumentParser()
    for name in ("root","independent","output"):
        ap.add_argument("--"+name,required=True,type=Path)
    args=ap.parse_args()
    report=verify(args.root,args.independent)
    write_exclusive(args.output,report)
    print(json.dumps({
        "state":"FOUR_WINDOW_INDEPENDENT_FINAL_AUDIT_PASS",
        "price_only_samples":report["eligible_price_only_predictions"],
        "censored":report["censored_windows"],
        "direction_correct":report["direction_correct_count"],
        "mean_abs_error_bps":report["mean_abs_forecast_error_bps"],
        "broker_orders_sent":0,"trade_authority":False
    }))
if __name__=="__main__":
    main()
