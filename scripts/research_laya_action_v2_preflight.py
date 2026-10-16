#!/usr/bin/env python3
"""Pocket4-only frozen v1 training artifact audit for future ACT calibration.

This program is read-only with respect to Laya trials/quotes/models and never
loads a GPU or starts a new training/evaluation/transaction.
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
from laya_cell.integrity import ContractError,read_json,sha_file,digest
from laya_cell.action_quality_research import probabilities
from laya_cell.resident_action_v2_research import MIN_EACH_LABEL,SCHEMA

MAX_EVAL_ROWS=90

def audit(candidate_dir,plan_dir,calibration):
    candidate_dir=Path(candidate_dir)
    plan_dir=Path(plan_dir)
    calibration=Path(calibration)
    manifest=candidate_dir/"candidate.json"
    plan_file=plan_dir/"plan.json"
    candidate=read_json(manifest,4*1024**2)
    plan=read_json(plan_file)
    if (candidate.get("candidate_id")!=digest(
            {k:v for k,v in candidate.items() if k!="candidate_id"}) or
            sha_file(candidate_dir/"adapter.safetensors")!=candidate.get("adapter_sha256") or
            plan.get("plan_id")!=digest({k:v for k,v in plan.items() if k!="plan_id"}) or
            plan.get("candidate_id")!=candidate["candidate_id"] or
            sha_file(manifest)!=plan.get("candidate_manifest_sha256") or
            plan.get("no_refit") is not True or
            candidate.get("pnl_reward_used") is not False or
            candidate.get("broker_orders_sent")!=0):
        raise ContractError("V2_ACTION_EMPIRICAL_FROZEN_CANDIDATE_CHANGED")
    counts=candidate.get("measurement",{}).get("action_target_counts",{})
    n=candidate.get("measurement",{}).get("rows")
    action=(counts.get("0"),counts.get("1"),counts.get("None"))
    if (type(n) is not int or n<16 or
            any(type(x) is not int or x<0 for x in action) or
            sum(action)!=n):
        raise ContractError("V2_ACTION_EMPIRICAL_TRAINING_LABEL_RECORD")
    accounting=candidate.get("row_accounting",{})
    ids=accounting.get("applied_row_ids")
    if (not isinstance(ids,list) or len(ids)!=n or len(ids)!=len(set(ids)) or
            accounting.get("applied_count")!=n or
            accounting.get("rolled_back_count")!=0 or
            candidate.get("action_update_steps")!=len(candidate.get("steps",[]))):
        raise ContractError("V2_ACTION_EMPIRICAL_ROW_ACCOUNTING")
    steps=candidate["steps"]
    if not steps or not all(s.get("applied") is True and
                            s.get("action_labels",0)>=0 and
                            isinstance(s.get("action_loss"),(int,float))
                            for s in steps):
        raise ContractError("V2_ACTION_EMPIRICAL_OPTIMIZER_RECEIPT")
    action_domination=[]
    for s in steps:
        primary=s.get("price_loss")
        total=s.get("loss")
        action_loss=s.get("action_loss")
        weight=candidate.get("objective",{}).get("action_loss_weight")
        if (not all(isinstance(v,(int,float)) for v in
                    (primary,total,action_loss,weight)) or
                weight<0 or total<=0 or action_loss<0):
            raise ContractError("V2_ACTION_EMPIRICAL_LOSS_COMPONENTS")
        action_domination.append(float(weight*action_loss/total))
    if plan.get("trial")!=39:
        # This particular empirical proof is scoped to one frozen candidate,
        # not to general promises about arbitrary other plans.
        raise ContractError("V2_ACTION_EMPIRICAL_TRIAL_CHANGED")
    old_report=read_json(calibration,16*1024**2)
    if (old_report.get("report_id")!=digest(
            {k:v for k,v in old_report.items()
             if k not in ("report_id","no_execution_or_model_modification")}) or
            old_report.get("claims",{}).get("automatic_trading") is not False or
            old_report.get("claims",{}).get("retrospective_only") is not True):
        raise ContractError("V2_ACTION_CLOSED_CALIBRATION_REPORT_CHANGED")
    tests=old_report["test_scores_after_selection"]
    for family in ("always_escalate","original_saturated_action_head",
                   old_report["validation_selected"]):
        if family not in tests:
            raise ContractError("V2_ACTION_MISSING_FROZEN_BASELINE")
    action_labels_supported=min(action[:2])>=MIN_EACH_LABEL
    # Current trial39 remains prospective and CANNOT be a training-calibration
    # source until CLOSED. We audit its saved predictions only.
    slots=[]
    for i in range(MAX_EVAL_ROWS):
        o=plan_dir/("outcome-%03d.json"%i)
        p=plan_dir/("prediction-%03d.json"%i)
        if not o.is_file():
            continue
        out=read_json(o,30*1024)
        if out.get("status")!="OBSERVED":
            slots.append({"slot":i,"status":out.get("status")});continue
        if not p.is_file() or out.get("prediction_file_sha256")!=sha_file(p):
            raise ContractError("V2_ACTION_PENDING_TRIAL_PREDICTION_CHANGED")
        pred=read_json(p,20*1024)
        if (pred.get("candidate_id")!=candidate["candidate_id"] or
                pred.get("plan_id")!=plan["plan_id"] or
                pred.get("slot")!=i or
                pred["created_at_ms"]>out.get("outcome_ms",0) or
                pred["created_at_ms"]>=pred["anchor_ms"]+300000):
            raise ContractError("V2_ACTION_PENDING_TRIAL_NOT_CAUSAL")
        act=probabilities(pred["candidate_act_escalate_probs"],2)
        correct=max(range(7),key=lambda j:pred["parent_probs"][j])==out["target_class"]
        brier=2*(act[0]-float(correct))**2
        if abs(brier-out["action_head_brier"])>1e-5:
            raise ContractError("V2_ACTION_PENDING_TRIAL_SCORE_CHANGED")
        slots.append({"slot":i,"status":"OBSERVED",
                      "action_top1":"ACT" if act[0]>=act[1] else "ESCALATE",
                      "parent_correct":correct,
                      "raw_action_brier":brier,
                      "candidate_brier":out["candidate_brier"],
                      "parent_brier":out["parent_brier"]})
    observed=[x for x in slots if x["status"]=="OBSERVED"]
    return {
        "schema":"heptatrader.laya-action-v2-train-only-preflight.v1",
        "at_ms":int(time.time()*1000),
        "existing_candidate_id":candidate["candidate_id"],
        "training_artifact_sha256":sha_file(manifest),
        "training_parent_correctness_label_counts":{
            "ACT_correct":action[0],"ESCALATE_wrong":action[1],
            "unavailable":action[2]},
        "true_training_row_count":n,
        "training_applied_steps":len(steps),
        "training_distinct_gradient_digests":len({s["gradient_digest"] for s in steps}),
        "training_mean_action_loss":sum(s["action_loss"] for s in steps)/len(steps),
        "training_mean_action_loss_fraction":sum(action_domination)/len(action_domination),
        "action_train_v2_is_supported_by_this_frozen_batch":action_labels_supported,
        "action_training_recommendation":(
            "BALANCED_TWO_CLASS_ACTION_RESEARCH_MAY_BE_PREPARED"
            if action_labels_supported else
            "DO_NOT_OPTIMIZE_ACTION_HEAD_WITH_SINGLE_LABEL; PRICE_ONLY_REMAINS_POSSIBLE"),
        "training_action_profile_schema":SCHEMA,
        "closed_calibration_report_id":old_report["report_id"],
        "closed_train_validation_test_split":old_report["counts"],
        "closed_selected_family":old_report["validation_selected"],
        "closed_holdout":tests,
        "pending_trial":39,
        "pending_trial_observed":len(observed),
        "pending_trial_censored":sum(x["status"]=="CENSORED" for x in slots),
        "pending_trial_model_act_suggestions":sum(x["action_top1"]=="ACT" for x in observed),
        "pending_trial_parent_correct":sum(x["parent_correct"] for x in observed),
        "pending_trial_action_brier_mean":(
            sum(x["raw_action_brier"] for x in observed)/len(observed) if observed else None),
        "pending_trial_price_brier_gain":(
            sum(x["parent_brier"]-x["candidate_brier"] for x in observed)/len(observed)
            if observed else None),
        "pending_trial_outcomes_not_used_for_model_fitting_or_selection":True,
        "original_candidate_or_trials_changed":False,
        "auto_trading":False,"broker_orders_sent":0,
        "next_v2_requires_explicit_new_frozen_training_objective":True,
        "future_statistical_or_economic_claim":False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate",type=Path,required=True)
    p.add_argument("--trial",type=Path,required=True)
    p.add_argument("--closed-calibration",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    os.umask(0o077)
    if not a.output.is_absolute() or a.output.exists() or a.output.is_symlink() or (
            a.output.parent.resolve() in (a.trial.resolve(),a.candidate.resolve())):
        p.error("new absolute output required outside existing candidate or trial")
    result=audit(a.candidate,a.trial,a.closed_calibration)
    fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(result,f,sort_keys=True,allow_nan=False,separators=(",",":"))
        f.write("\n");f.flush();os.fsync(f.fileno())
    print(json.dumps({key:value for key,value in result.items()
                      if key!="closed_holdout"},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
