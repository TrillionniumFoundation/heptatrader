#!/usr/bin/env python3
"""Read-only causality and 60-second settlement audit for frozen PAPER shadows.

Reads sealed forecast/outcome receipts and freshly verifies original quote
archives. Does NOT overwrite the original 90-slot trial or trade/fit/promote.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research/neuron_learning"))
from laya_cell.integrity import read_json,sha_file,ContractError
from laya_cell.original_quote_asof_finality import verify_original_input,verify_original_outcome
from laya_cell.price_original_campaign import check_plan,verify_saved_outcome,state_summary,SLOTS
from laya_cell.resident_recent_source import read_recent_original_quotes
from laya_cell.resident_data import read_quotes


def audit(config,campaign,now_ms):
    plan_path=campaign/"plan.json"
    plan=check_plan(read_json(plan_path))
    if (sha_file(config["file"])!=plan["pinned_sources"]["source_config"] or
            sha_file(Path(config["data"]["period_path"]))!=config["data"]["period_sha256"]):
        raise ContractError("ASOF_AUDIT_ORIGINAL_CONFIG_CHANGED")
    source_files={
        "original_bridge": ROOT/"research/neuron_learning/laya_cell/price_original_live_features.py",
        "contract_module": ROOT/"research/neuron_learning/laya_cell/price_original_campaign.py",
    }
    for key,path in source_files.items():
        if sha_file(path)!=plan["pinned_sources"][key]:
            raise ContractError("ASOF_AUDIT_PINNED_SOURCE_CHANGED_"+key)
    try:
        quotes,prefixes=read_recent_original_quotes(config["data"],now_ms)
        reader="VERIFIED_RECENT_ORIGINAL_QUOTES"
    except ContractError as exc:
        if str(exc) not in (
                "RECENT_SOURCE_CANNOT_SKIP_RECENT_ARCHIVE",
                "RECENT_SOURCE_NO_UNSEALED_TAIL",
                "RECENT_SOURCE_INSUFFICIENT_HORIZON_COVERAGE"):
            raise
        quotes,prefixes=read_quotes(config["data"],now_ms)
        reader="VERIFIED_COMPLETE_ORIGINAL_ARCHIVE"
    if len(quotes)>150000:
        raise ContractError("ASOF_AUDIT_QUOTE_CAP")
    checked=[]
    outcomes=[]
    status={"inputs_match":0,"inputs_unreconstructable":0,
            "observed_feasible":0,"observed_inconsistent":0,
            "censored":0,"sealed_unsettled":0,
            "fixed_60s_grace_finality_changed_class":0,
            "fixed_60s_grace_finality_changed_close":0}
    for slot in range(SLOTS):
        predpath=campaign/("prediction-%03d.json"%slot)
        outpath=campaign/("outcome-%03d.json"%slot)
        pred=read_json(predpath) if predpath.is_file() else None
        outcome=read_json(outpath) if outpath.is_file() else None
        if pred is None and outcome is None:
            continue
        if pred is None:
            verify_saved_outcome(plan,slot,outcome,None,None)
            status["censored"]+=1
            outcomes.append(outcome)
            checked.append({"slot":slot,"state":"CENSORED_NO_FORECAST"})
            continue
        entry={"slot":slot}
        try:
            entry["precommitted_input"]=verify_original_input(pred,quotes,prefixes)
            status["inputs_match"]+=1
        except ContractError as exc:
            entry["precommitted_input"]={"state":"UNRECONSTRUCTABLE",
                                         "reason":str(exc)[:140]}
            status["inputs_unreconstructable"]+=1
        if outcome is None:
            status["sealed_unsettled"]+=1
            entry["state"]="FORECAST_SEALED_AWAITING_OUTCOME"
            checked.append(entry);continue
        verify_saved_outcome(plan,slot,outcome,pred,sha_file(predpath))
        outcomes.append(outcome)
        if outcome["status"]=="CENSORED":
            status["censored"]+=1
            entry["state"]="CENSORED_WITH_FORECAST"
            checked.append(entry);continue
        trial=verify_original_outcome(pred,outcome,quotes,now_ms)
        entry["original_saved_outcome"]=trial
        entry["state"]=trial["status"]
        if trial["status"]=="RECEIPT_MATCH_CAUSAL_ASOF_FEASIBLE":
            status["observed_feasible"]+=1
            finality=trial["matured_60s_finality"]
            if finality["class_changed"] is True:
                status["fixed_60s_grace_finality_changed_class"]+=1
            if finality["quote_receipt_changed"] is True:
                status["fixed_60s_grace_finality_changed_close"]+=1
        else:
            status["observed_inconsistent"]+=1
        checked.append(entry)
    original=state_summary(plan,outcomes)
    return {"schema":"heptatrader.original-price-asof-audit-readonly.v1",
            "audited_at_ms":now_ms,"original_plan_sha256":sha_file(plan_path),
            "original_plan_id":plan["plan_id"],
            "original_model_sha256":plan["pinned_sources"]["model_weights"],
            "verified_source_kind":reader,"source_quote_count":len(quotes),
            "source_prefix_population":len(prefixes),
            "original_observed":original["observed"],"original_censored":original["censored"],
            "original_recorded":original["recorded"],
            "audit_status":status,"per_slot":checked,
            "inputs_mathematically_rebuilt_not_original_prefix_snapshot_proven":True,
            "source_read_start_not_persisted_in_original_v1":True,
            "all_actual_outcomes_consistent_with_some_causal_source_read":
                status["observed_inconsistent"]==0 and status["inputs_unreconstructable"]==0,
            "no_original_trial_mutation":True,"no_orders":True,
            "no_efficacy_or_trade_claim":True}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config",required=True,type=Path)
    ap.add_argument("--campaign",required=True,type=Path)
    ap.add_argument("--output",required=True,type=Path)
    args=ap.parse_args()
    os.umask(0o077)
    if (not args.output.is_absolute() or args.output.exists() or
            not args.output.parent.is_dir() or args.output.is_symlink() or
            not args.campaign.is_absolute() or args.campaign.is_symlink() or
            args.output.parent.resolve()==args.campaign.resolve()):
        ap.error("new absolute output outside original campaign is required")
    config={"file":args.config,"data":read_json(args.config)}
    report=audit(config,args.campaign,int(time.time()*1000))
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(report,f,sort_keys=True,allow_nan=False,separators=(",",":"))
        f.write("\n");f.flush();os.fsync(f.fileno())
    print("ORIGINAL_QUOTE_FINALITY_CHECK",json.dumps({
        k:v for k,v in report.items() if k!="per_slot"},sort_keys=True),flush=True)
    if not report["all_actual_outcomes_consistent_with_some_causal_source_read"]:
        raise SystemExit(2)


if __name__=="__main__":
    main()
