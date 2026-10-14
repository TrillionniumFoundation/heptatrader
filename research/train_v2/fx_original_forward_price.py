"""Four preregistered ORIGINAL IB PAPER quote-only price/cost windows.

Standalone *research* prospective study. No forecast model, order API, native
Train V2 product registration, actual broker fill, production signing, trading
authority, or auto-promotion. The 120-minute horizon is deliberately OUTSIDE
Train V2's existing <=60-minute FeatureSpec upper bound; never mislabel this
study a native Train V2 admission.
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
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from historical_training.common import read, sha
from .fx_ridge_original import (
    LOOKBACK, MAX_DECISION_LATENCY_MS, bar_close_features, cost_hurdle,
    newest_source_chain, scope,
)
from .fx_ridge_outcome import verify_original_prefixes
from .fx_original_native_dataset import first_mature_original, midpoint

SCHEMA = "heptatrader.fx-original-future-price-cost-calendar.v1"
COUNT = 4
HORIZONS_MINUTES = (15, 30, 60, 120)
HORIZONS_MS = tuple(h * 60000 for h in HORIZONS_MINUTES)
GRID_MS = 300000
STEP_MS = 195 * 60000  # >= 60min causal lookback +120min outcome +5min purge
REGISTER_LEAD_MS = 20 * 60000
DECISION_START_MS = 1000
DECISION_END_MS = 4500
OUTCOME_OFFSET_MS = 6500
OUTCOME_DEADLINE_MS = 15000
QUOTE_TOLERANCE_MS = 5000
SOURCE_READY_MAX_STALENESS_MS = 30000
SOCKET_OR_BROKER_OPERATIONS = False

class Hold(ValueError): pass

def require(ok,why):
    if not ok: raise Hold(why)

def now_ms():
    return time.time_ns()//1000000

def code_sha():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def private_root(path, *,create=False):
    p=Path(path)
    require(p.is_absolute() and ".." not in p.parts and not p.is_symlink(),
            "RESEARCH_DIRECTORY_INVALID")
    if create:
        require(not p.exists(),"EXISTING_RESEARCH_ROOT_CANNOT_BE_REUSED")
        p.mkdir(parents=True,mode=0o700)
    s=p.stat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid()
            and stat.S_IMODE(s.st_mode)==0o700,"RESEARCH_DIRECTORY_NOT_PRIVATE")
    return p

def write_record(path, data):
    """Write exactly once, flush file and directory; return durable time."""
    path=Path(path)
    private_root(path.parent)
    require(not path.exists() and not path.is_symlink(),"EVIDENCE_ALREADY_EXISTS_NO_RETRY")
    raw=canonical(data)+b"\n"
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    except BaseException:
        # Never delete a possibly partly written evidence file on crash.
        raise
    return now_ms()

def frozen_anchors(registered_at):
    require(type(registered_at) is int and registered_at>0,
            "REGISTRATION_CLOCK_INVALID")
    first=((registered_at+REGISTER_LEAD_MS+GRID_MS-1)//GRID_MS)*GRID_MS
    anchors=[first+i*STEP_MS for i in range(COUNT)]
    require(all(a%GRID_MS==0 for a in anchors)
            and all(b-a>HORIZONS_MS[-1]+(LOOKBACK-1)*GRID_MS
                    for a,b in zip(anchors,anchors[1:])),
            "PROSPECTIVE_ANCHORS_NOT_NONOVERLAPPING")
    return anchors

def frozen_input_spec(old_root, period_sha):
    original=Path(old_root)
    prior_manifest=read(original/"window-00"/"manifest.json",8192)
    original_spec=read(original/"window-00"/"feature-spec.json",8192)
    require(prior_manifest.get("original_period_sha256")==period_sha
            and prior_manifest.get("source_kind")=="LOCAL_SHA_CHAINED_ORIGINAL_IB_PAPER"
            and original_spec["period_sha256"]==period_sha
            and original_spec["instrument"]=="EUR.USD"
            and original_spec["horizon_ms"]==900000
            and original_spec.get("quote_expiry_policy") is not None
            and original_spec["trade_authority"] is False
            and original_spec["promotion_allowed"] is False,
            "READONLY_SOURCE_SPEC_IDENTITY_INVALID")
    return original_spec,sha(original/"window-00"/"feature-spec.json")

def preseal(root, config_path, cost_path, prior_campaign_root, readiness_path):
    config_path,cost_path,prior_campaign_root,readiness_path=map(
        Path,(config_path,cost_path,prior_campaign_root,readiness_path))
    config,period_sha,cost,cost_sha=scope(config_path,cost_path)
    spec,spec_sha=frozen_input_spec(prior_campaign_root,period_sha)
    require(readiness_path.is_file() and not readiness_path.is_symlink(),
            "ORIGINAL_READINESS_PRODUCER_UNAVAILABLE")
    frozen=now_ms()
    anchors=frozen_anchors(frozen)
    root=private_root(root,create=True)
    plan={
        "schema":SCHEMA,
        "intent":"FUTURE_ORIGINAL_QUOTE_PRICE_AND_COST_ONLY_NO_MODEL",
        "registered_at_ms":frozen,
        "anchors_ms":anchors,
        "fixed_windows":COUNT,
        "independent_horizons_ms":list(HORIZONS_MS),
        "grid_ms":GRID_MS,"spacing_ms":STEP_MS,
        "first_decision_offset_ms":DECISION_START_MS,
        "last_decision_offset_ms":DECISION_END_MS,
        "maturity_first_quote_max_lag_ms":QUOTE_TOLERANCE_MS,
        "maturity_attempt_delay_ms":OUTCOME_OFFSET_MS,
        "maturity_attempt_deadline_ms":OUTCOME_DEADLINE_MS,
        "input_quote_lookback":LOOKBACK,
        "source_ready_state_required":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
        "source_minimum_usable":20,"source_maximum_missing":4,
        "source_freshness_age_max_ms":SOURCE_READY_MAX_STALENESS_MS,
        "readiness_file":str(readiness_path),
        "config_file":str(config_path),"config_sha256":sha(config_path),
        "cost_file":str(cost_path),"cost_sha256":cost_sha,
        "source_input_spec_file":str(prior_campaign_root/"window-00"/"feature-spec.json"),
        "source_input_spec_sha256":spec_sha,
        "period_sha256":period_sha,
        "original_feed_root":config["feed_directory"],
        "research_code_sha256":code_sha(),
        "old_censored_window_still_censored":True,
        "frozen_cost_stress_no_reoptimization":True,
        "no_model_predictions_or_original_policy_propensities":True,
        "future_ib_market_session_schedule_attested":False,
        "source_independently_ib_signed":False,
        "local_registration_is_not_native_owner_or_independent_witness":True,
        "not_native_train_v2_120min_spec":True,
        "model_fit_authorized":False,
        "behavior_policy":"NO_TRADE",
        "trade_authority":False,"actor_update":False,
        "automatic_training":False,"model_promotion_allowed":False,
        "broker_queries_sent":0,"broker_orders_sent":0,
    }
    write_record(root/"plan.json",plan)
    receipts=[]
    for i,anchor in enumerate(anchors):
        cell=root/("window-%02d"%i)
        cell.mkdir(mode=0o700)
        receipts.append({
            "index":i,"anchor_ms":anchor,
            "entry_first_ms":anchor+DECISION_START_MS,
            "entry_last_ms":anchor+DECISION_END_MS,
            "mature_at_ms":[anchor+h for h in HORIZONS_MS],
            "feature_policy":"ORIGINAL_PREGRID_13_QUOTE_PSEUDOCLOSE",
        })
    sealed={
        "schema":SCHEMA+".sealed.v1",
        "plan_sha256":sha(root/"plan.json"),
        "research_code_sha256":code_sha(),
        "windows":receipts,
        "sealed_before_all_observations":True,
        "all_expected_maturities_unknown_at_registration":True,
        "broker_orders_sent":0,"native_registration_admitted":False,
        "model_promotion_allowed":False,
    }
    complete=write_record(root/"sealed.json",sealed)
    require(complete<anchors[0]-10*60000,"REGISTRATION_NOT_SAFELY_BEFORE_FIRST_OBSERVATION")
    return {"state":"FOUR_FUTURE_PRICE_WINDOWS_FROZEN_NOT_NATIVE_TRAIN_V2",
            "registered_at_ms":frozen,"durably_sealed_at_ms":complete,
            "anchors_utc":[datetime.fromtimestamp(a/1000,timezone.utc).isoformat()
                           for a in anchors],
            "cost_sha256":cost_sha,"seal_sha256":sha(root/"sealed.json"),
            "source_ready_during_preseal_not_AN_admission":True,
            "trade_authority":False,"broker_orders_sent":0}

def verify(root):
    root=private_root(root)
    plan=read(root/"plan.json",32768)
    sealed=read(root/"sealed.json",16384)
    require(plan.get("schema")==SCHEMA and sealed.get("schema")==SCHEMA+".sealed.v1"
            and sha(root/"plan.json")==sealed["plan_sha256"]
            and plan.get("research_code_sha256")==sealed["research_code_sha256"]==code_sha()
            and plan.get("model_fit_authorized") is False
            and plan.get("model_promotion_allowed") is False
            and plan.get("trade_authority") is False
            and plan.get("broker_orders_sent")==0
            and plan.get("automatic_training") is False
            and plan.get("not_native_train_v2_120min_spec") is True
            and plan.get("behavior_policy")=="NO_TRADE"
            and plan.get("independent_horizons_ms")==list(HORIZONS_MS)
            and plan.get("anchors_ms")==frozen_anchors(plan["registered_at_ms"])
            and sealed.get("sealed_before_all_observations") is True
            and len(sealed.get("windows",[]))==COUNT,
            "FROZEN_RESEARCH_PLAN_NOT_QUALIFIED")
    config,period_sha,cost,cost_sha=scope(
        plan["config_file"],plan["cost_file"])
    spec,spec_sha=frozen_input_spec(
        Path(plan["source_input_spec_file"]).parent.parent,period_sha)
    require(sha(plan["config_file"])==plan["config_sha256"]
            and cost_sha==plan["cost_sha256"]
            and spec_sha==plan["source_input_spec_sha256"]
            and config["feed_directory"]==plan["original_feed_root"]
            and period_sha==plan["period_sha256"],
            "FROZEN_RESEARCH_INPUT_CHANGED")
    for i,w in enumerate(sealed["windows"]):
        require(w.get("index")==i and w.get("anchor_ms")==plan["anchors_ms"][i]
                and w.get("entry_first_ms")==w["anchor_ms"]+DECISION_START_MS
                and w.get("entry_last_ms")==w["anchor_ms"]+DECISION_END_MS
                and w.get("mature_at_ms")==[w["anchor_ms"]+h for h in HORIZONS_MS],
                "FROZEN_WINDOW_IDENTITY_CHANGED")
        private_root(root/("window-%02d"%i))
    return plan,sealed,config,cost,spec

def readiness_observation(plan,at_ms):
    # Health is independent read-only source triage, NOT trading authorization.
    path=Path(plan["readiness_file"])
    data=read(path,131072)
    count=data.get("counts") or {}
    stamp=data.get("at_ms")
    usable=count.get("USABLE",0)
    missing=sum(x for k,x in count.items() if k!="USABLE")
    if (type(stamp) is not int or not 0<=at_ms-stamp<=SOURCE_READY_MAX_STALENESS_MS
        or data.get("state")!="READY_FOR_REVIEW_NOT_AUTHORIZATION"
        or type(usable) is not int or usable<20
        or missing>4 or data.get("automatic_trial_registration") is not False):
        raise Hold("ORIGINAL_READINESS_HOLD_OR_STALE")
    return {"health_at_ms":stamp,"state":data["state"],
            "usable_probes":usable,"missing_probes":missing,
            "local_health_snapshot_sha256":sha(path)}

def attempt_record(root,cell,stage,state,reason,extra=None):
    record={
        "schema":SCHEMA+".attempt.v1",
        "stage":stage,"state":state,"reason_code":reason,
        "attempt_recorded_at_ms":now_ms(),
        "automatic_retry_allowed":False,
        "historical_price_or_future_quote_backfilled":False,
        "trade_authority":False,"broker_queries_sent":0,
        "broker_orders_sent":0,"model_promotion_allowed":False,
    }
    if extra:
        record.update(extra)
    write_record(cell/(stage+"-attempt.json"),record)
    return record

def prepare_entry(root,plan,w,config,cost,spec,*,at_ms=None,
                  source_fn=newest_source_chain,
                  features_fn=bar_close_features, readiness_fn=readiness_observation):
    cell=root/("window-%02d"%w["index"])
    require(not (cell/"decision-attempt.json").exists(),"DECISION_ALREADY_ATTEMPTED")
    actual=now_ms() if at_ms is None else at_ms
    anchor=w["anchor_ms"]
    if not w["entry_first_ms"]<=actual<=w["entry_last_ms"]:
        return attempt_record(root,cell,"decision","CENSORED",
                              "MISSED_FROZEN_PREACTION_DEADLINE")
    try:
        health=readiness_fn(plan,actual)
        rows,proofs=source_fn(config,spec,actual)
        samples,features=features_fn(rows,anchor)
        require(len(samples)==LOOKBACK,"ORIGINAL_INPUT_LOOKBACK_NOT_13")
        latest=samples[-1]
        require(latest["event_at_ms"]<anchor
                and latest["available_at_ms"]<=anchor
                and latest["available_at_ms"]<=actual<latest["valid_until_ms"]
                and 0<=actual-latest["available_at_ms"]<=MAX_DECISION_LATENCY_MS,
                "ORIGINAL_QUOTE_AGE_EXPIRY_OR_CAUSALITY")
        require(len(features)==8
                and all(math.isfinite(float(v)) for v in features),
                "FROZEN_FEATURES_NOT_FINITE_8D")
        hurdle=cost_hurdle(latest["bid"],latest["ask"],cost)
        prepared={
            "schema":SCHEMA+".preaction.v1",
            "anchor_ms":anchor,"decision_at_ms":actual,
            "intended_behavior":"NO_TRADE_PRICE_ONLY",
            "no_model_prediction_or_propensity_derived":True,
            "source_health":health,
            "input_source_ids":[s["source_id"] for s in samples],
            "input_quotes_available_before_grid":True,
            "input_quote_bid":latest["bid"],"input_quote_ask":latest["ask"],
            "input_quote_event_at_ms":latest["event_at_ms"],
            "input_quote_available_at_ms":latest["available_at_ms"],
            "input_quote_expiry_ms":latest["valid_until_ms"],
            "feature_vector":list(map(float,features)),
            "cost_stress":hurdle,
            "source_prefix_receipts":proofs,
            "same_original_period_sha256":plan["period_sha256"],
            "native_owner_witness_or_root_enrolled":False,
            "trade_authority":False,"broker_orders_sent":0,
        }
        write_record(cell/"preaction.json",prepared)
        sealed_at=now_ms()
        require(sealed_at<=w["entry_last_ms"] and
                sealed_at<latest["valid_until_ms"],"ORIGINAL_PREACTION_SEALED_TOO_LATE")
        # Distinct final receipt: successful maturity must additionally match
        # this frozen preaction SHA and exact original source prefixes.
        return attempt_record(root,cell,"decision","FROZEN_ORIGINAL_PRICE_ONLY",
                "NONE",{"preaction_sha256":sha(cell/"preaction.json"),
                        "original_quote_expiry_ms":latest["valid_until_ms"],
                        "original_preaction_durable_at_ms":sealed_at})
    except Exception as err:
        # No repeated collection. When partial preaction exists, retain it
        # unchanged as evidence of a rejected/too-late attempt.
        reason=("EXCEPTION_"+type(err).__name__+"_"+str(err))[:175]
        return attempt_record(root,cell,"decision","CENSORED",reason)

def mature_horizon(root,plan,w,horizon,config,spec,*,at_ms=None,
                   source_fn=newest_source_chain,
                   prefix_verify_fn=verify_original_prefixes):
    require(horizon in HORIZONS_MINUTES,"HORIZON_NOT_FROZEN")
    cell=root/("window-%02d"%w["index"])
    stage="maturity-%03d"%horizon
    require(not (cell/(stage+"-attempt.json")).exists(),
            "MATURITY_ALREADY_ATTEMPTED")
    actual=now_ms() if at_ms is None else at_ms
    due=w["anchor_ms"]+horizon*60000
    if not due+OUTCOME_OFFSET_MS<=actual<=due+OUTCOME_DEADLINE_MS:
        return attempt_record(root,cell,stage,"CENSORED",
                              "OUTSIDE_FROZEN_MATURITY_ATTEMPT_WINDOW")
    prior_path=cell/"decision-attempt.json"
    if not prior_path.is_file():
        return attempt_record(root,cell,stage,"CENSORED",
                              "ORIGINAL_ENTRY_NOT_FROZEN")
    previous=read(prior_path)
    if previous.get("state")!="FROZEN_ORIGINAL_PRICE_ONLY":
        return attempt_record(root,cell,stage,"CENSORED",
                              "NO_ELIGIBLE_ORIGINAL_PREACTION")
    try:
        preaction=read(cell/"preaction.json",131072)
        require(previous["preaction_sha256"]==sha(cell/"preaction.json")
                and preaction["anchor_ms"]==w["anchor_ms"]
                and previous["original_preaction_durable_at_ms"] <
                    previous["original_quote_expiry_ms"]
                and preaction["same_original_period_sha256"]==plan["period_sha256"]
                and preaction["trade_authority"] is False,
                "FROZEN_ORIGINAL_PREACTION_CHANGED")
        prefix_verify_fn(preaction["source_prefix_receipts"])
        rows,proofs=source_fn(config,spec,actual)
        timestamps=[q["event_at_ms"] for q in rows]
        require(timestamps==sorted(set(timestamps)),
                "ORIGINAL_MATURE_QUOTES_NOT_UNIQUE_SORTED")
        # The original quote decoder filters quote availability as of actual.
        seen=[q for q in rows if q["available_at_ms"]<=actual]
        q,reason=first_mature_original(seen,[x["event_at_ms"] for x in seen],due)
        require(q is not None,"ORIGINAL_MATURITY_"+reason)
        with localcontext() as ctx:
            ctx.prec=40
            bid=Decimal(preaction["input_quote_bid"])
            ask=Decimal(preaction["input_quote_ask"])
            end_bid=Decimal(q["bid"])
            end_ask=Decimal(q["ask"])
            require(0<bid<=ask and 0<end_bid<=end_ask,"ORIGINAL_BID_ASK_CROSSED")
            mid_in=(bid+ask)/2
            mid_out=(end_bid+end_ask)/2
            price_only=(mid_out/mid_in-1)*Decimal(10000)
            long_quote_side=(end_bid-ask)/mid_in*Decimal(10000)
            short_quote_side=(bid-end_ask)/mid_in*Decimal(10000)
        return_data={
            "schema":SCHEMA+".mature-quote.v1",
            "anchor_ms":w["anchor_ms"],"horizon_minutes":horizon,
            "due_ms":due,
            "first_original_mature_source_id":q["source_id"],
            "first_mature_quote_event_ms":q["event_at_ms"],
            "first_mature_quote_available_ms":q["available_at_ms"],
            "preaction_sha256":previous["preaction_sha256"],
            "midpoint_move_bps":str(price_only),
            "hypothetical_long_entry_ask_exit_bid_move_bps":str(long_quote_side),
            "hypothetical_short_entry_bid_exit_ask_move_bps":str(short_quote_side),
            "source_prefix_receipts":proofs,
            "quote_side_prices_are_NOT_guaranteed_fills":True,
            "financing_and_realized_broker_net_pnl_not_attested":True,
            "selected_trading_action":"NO_TRADE",
            "actual_broker_execution_pnl_samples":0,
            "model_training_or_promotion_qualified":False,
            "trade_authority":False,"broker_orders_sent":0,
        }
        write_record(cell/(stage+".json"),return_data)
        return attempt_record(root,cell,stage,"MATURE_ORIGINAL_QUOTE_PRICE_ONLY",
                              "NONE",{"quote_result_sha256":sha(cell/(stage+".json"))})
    except Exception as err:
        return attempt_record(root,cell,stage,"CENSORED",
                 ("EXCEPTION_"+type(err).__name__+"_"+str(err))[:175])

def sleep_to(time_ms):
    while now_ms()<time_ms:
        remaining=time_ms-now_ms()
        time.sleep(min(3.0,max(0.001,(remaining-25)/1000)))

def worker(root):
    root=private_root(root)
    plan,sealed,config,cost,spec=verify(root)
    require(now_ms()<sealed["windows"][0]["entry_first_ms"],
            "WORKER_STARTED_AFTER_FIRST_DECISION")
    require(not (root/"worker-start.json").exists(),
            "WORKER_ALREADY_STARTED")
    write_record(root/"worker-start.json",{
        "schema":SCHEMA+".worker.v1",
        "started_at_ms":now_ms(),
        "sealed_sha256":sha(root/"sealed.json"),
        "no_restart_no_retry":True,
        "broker_orders_sent":0,"no_trading_or_model_promotion":True,
    })
    print(json.dumps({"state":"FINITE_FUTURE_ORIGINAL_RESEARCH_RUNNING",
                      "windows":COUNT,"broker_orders_sent":0}),flush=True)
    result=[]
    for w in sealed["windows"]:
        sleep_to(w["entry_first_ms"])
        entry=prepare_entry(root,plan,w,config,cost,spec)
        print(json.dumps({"window":w["index"],"stage":"decision",
                          "state":entry["state"],"reason":entry["reason_code"]}),
              flush=True)
        sub={"index":w["index"],"decision_state":entry["state"],
             "decision_attempt_sha256":sha(root/("window-%02d"%w["index"])/"decision-attempt.json"),
             "outcomes":[]}
        for minutes in HORIZONS_MINUTES:
            sleep_to(w["anchor_ms"]+minutes*60000+OUTCOME_OFFSET_MS)
            outcome=mature_horizon(root,plan,w,minutes,config,spec)
            print(json.dumps({"window":w["index"],
                              "stage":"maturity-%03d"%minutes,
                              "state":outcome["state"],
                              "reason":outcome["reason_code"]}),flush=True)
            sub["outcomes"].append({
                "horizon_minutes":minutes,"state":outcome["state"],
                "attempt_sha256":sha(root/("window-%02d"%w["index"])/
                                        ("maturity-%03d-attempt.json"%minutes))})
        result.append(sub)
    write_record(root/"final.json",{
        "schema":SCHEMA+".final.v1",
        "sealed_sha256":sha(root/"sealed.json"),
        "completed_at_ms":now_ms(),
        "windows":result,
        "window_count":COUNT,
        "original_preaction_frozen_count":sum(x["decision_state"]=="FROZEN_ORIGINAL_PRICE_ONLY"
                                               for x in result),
        "mature_quote_only_samples":sum(y["state"]=="MATURE_ORIGINAL_QUOTE_PRICE_ONLY"
                                         for x in result for y in x["outcomes"]),
        "all_historical_test_and_loss_facts_unchanged":True,
        "no_model_predictions_or_production_admission":True,
        "broker_orders_sent":0,"trading_strategy_changed":False,
        "model_promotion_allowed":False,"actual_execution_rewards":0,
    })
    return {"state":"FINITE_FUTURE_PRICE_OBSERVATION_COMPLETE_NOT_ALPHA",
            "windows":COUNT,"broker_orders_sent":0}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode",required=True,choices=("preseal","verify","run"))
    p.add_argument("--root",type=Path,required=True)
    for k in ("config","cost","prior-campaign","readiness"):
        p.add_argument("--"+k,type=Path)
    a=p.parse_args()
    os.umask(0o077)
    if a.mode=="preseal":
        require(all((a.config,a.cost,a.prior_campaign,a.readiness)),
                "PRESEAL_ARGUMENTS_MISSING")
        out=preseal(a.root,a.config,a.cost,a.prior_campaign,a.readiness)
    elif a.mode=="verify":
        pl,s,c,co,sp=verify(a.root)
        out={"state":"RESEARCH_PLAN_HASHES_AND_CALENDAR_VALID",
             "windows":len(s["windows"]),
             "not_native_train_v2_120min_spec":True,
             "no_trading":True}
    else:
        out=worker(a.root)
    print(json.dumps(out,sort_keys=True),flush=True)
if __name__=="__main__":main()
