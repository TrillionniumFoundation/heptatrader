#!/usr/bin/env python3
"""Pocket4-only frozen Tiny MLP: real original-source 90-slot research campaign.

Every model forecast is sealed BEFORE its five-minute future outcome.
NEVER trades, promotes Laya, edits the existing source-prior Shadow campaign,
or rewrites fixed missed and censored observations.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research/neuron_learning"))
from laya_cell.integrity import ContractError,read_json,sha_file,digest
from laya_cell.resident_store import write_once
from laya_cell.resident_data import read_quotes
from laya_cell.resident_recent_source import read_recent_original_quotes
from laya_cell.price_original_live_features import original_closed_bars,original_feature_snapshot
from laya_cell.price_year_gpu import (features_from_bars,fold_indices,class_prior,FEATURE_NAMES)
from laya_cell.price_original_campaign import (
    SLOTS,HORIZON_MS,INPUT_GRACE_MS,SLOT_SPACING_MS,
    check_plan,create_plan,expected_anchor,prediction,grade,state_summary,
    verify_saved_outcome,pending_actions)
from research_laya_price_year_gpu import model_for,metric_torch

STOP=False
def stop(*_):
    global STOP
    STOP=True


def write_status(path,data):
    path=Path(path)
    tmp=path.with_name("."+path.name+"."+str(os.getpid()))
    with tmp.open("x") as f:
        json.dump(data,f,sort_keys=True,allow_nan=False,separators=(",",":"))
        f.write("\n");f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def get_original_quotes(c,asof,anchor,*,full=False):
    """Only already-known original receipts; full fallback retains archival checks."""
    import bisect
    cutoff=anchor-15*SLOT_SPACING_MS
    if not full:
        try:
            quotes,pins=read_recent_original_quotes(c,asof)
            return [x for x in quotes if x["t"]>=cutoff],pins
        except ContractError as exc:
            text=str(exc)
            # Availability-only fallback. An archive changing mid-read,
            # proof mismatch, or corrupted footer must NEVER be upgraded
            # to a success merely by trying a different reader.
            if text not in (
                "RECENT_SOURCE_NO_UNSEALED_TAIL",
                "RECENT_SOURCE_INSUFFICIENT_EVIDENCE",
                "RECENT_SOURCE_INSUFFICIENT_HORIZON_COVERAGE",
                "RECENT_SOURCE_CANNOT_SKIP_RECENT_ARCHIVE",
            ):
                raise
    quotes,pins=read_quotes(c,asof)
    t=[x["t"] for x in quotes]
    pos=bisect.bisect_left(t,cutoff)
    return quotes[pos:],pins


def asof_feature(c,asof,anchor):
    # Prefer archive-pin checked recent tail, but recover strict 13-bar
    # coverage from fully verified original history after source rotation.
    try:
        quotes,pins=get_original_quotes(c,asof,anchor)
        feature=original_feature_snapshot(quotes,pins,asof,require_fresh=True)
        return feature,quotes,pins
    except ContractError as exc:
        if str(exc) not in ("ORIGINAL_FEATURE_INSUFFICIENT_CLOSED_HISTORY",
                            "ORIGINAL_FEATURE_NONCONTIGUOUS_BARS",
                            "ORIGINAL_FEATURE_INPUT_LATE_OR_UNCOMMITTED"):
            raise
    quotes,pins=get_original_quotes(c,asof,anchor,full=True)
    return original_feature_snapshot(quotes,pins,asof,require_fresh=True),quotes,pins



def poll_due_outcome_once(config,plan,slot,pred):
    """One source read, re-checking the REAL clock after it completes.

    Entering the source read inside grace does not entitle a delayed
    post-grace write to an OBSERVED receipt. The time passed to grade()
    is the time the score can actually be committed.
    """
    anchor=expected_anchor(plan,slot)
    source_asof=int(time.time()*1000)
    quotes,pins=get_original_quotes(config,source_asof,anchor)
    bars,receipts=original_closed_bars(quotes,source_asof)
    commit_ms=int(time.time()*1000)
    return grade(plan,slot,pred,bars,receipts,commit_ms,digest(pins))

def prepare_assets(a,c):
    import torch
    from safetensors.torch import load_file
    torch.set_num_threads(2)
    year=Path(a.year_evidence)
    protocol=read_json(year/"protocol.json")
    selection=read_json(year/"fold-13-selection.json")
    bars=read_json(a.bars,32*1024**2)
    folds=read_json(a.folds)
    model_file=year/"fold-13-tiny_mlp.safetensors"
    pins={
        "revised_bars":sha_file(a.bars),
        "frozen_folds":sha_file(a.folds),
        "model_weights":sha_file(model_file),
        "frozen_selection":sha_file(year/"fold-13-selection.json"),
        "frozen_protocol":sha_file(year/"protocol.json"),
        "feature_module":sha_file(ROOT/"research/neuron_learning/laya_cell/price_year_gpu.py"),
        "original_bridge":sha_file(ROOT/"research/neuron_learning/laya_cell/price_original_live_features.py"),
        "contract_module":sha_file(ROOT/"research/neuron_learning/laya_cell/price_original_campaign.py"),
        "runner":sha_file(Path(__file__)),
        "source_config":sha_file(a.config)}
    if (pins["revised_bars"]!=protocol["historical_bar_sha256"] or
            pins["frozen_folds"]!=protocol["original_fold_source_sha256"] or
            pins["feature_module"]!=protocol["feature_source_sha256"] or
            pins["model_weights"]!=selection["training"]["tiny_mlp"]["adapter_sha256"] or
            selection["selected_before_test"]!="tiny_mlp" or
            protocol.get("fold_count")!=14 or
            bars.get("research_only_revised_not_contemporaneous") is not True or
            c.get("automatic_trading") is not False or
            c.get("canonical_owner_endpoint") is not None or
            sha_file(Path(c["period_path"]))!=c["period_sha256"]):
        raise ContractError("ORIGINAL_CAMPAIGN_FROZEN_YEAR_OR_SOURCE_IDENTITY")
    features=features_from_bars(bars["bars"])
    folded=[row for row in folds["folds"] if row["fold"]==13]
    if len(folded)!=1:raise ContractError("ORIGINAL_CAMPAIGN_FOLD_13")
    indices=fold_indices(features,folded[0])
    if {k:len(v) for k,v in indices.items()}!=selection["counts"]:
        raise ContractError("ORIGINAL_CAMPAIGN_YEAR_COUNTS_CHANGED")
    x=torch.tensor(features["features"],dtype=torch.float32,device="cpu")
    y=torch.tensor(features["targets"],dtype=torch.long,device="cpu")
    xtr=x[indices["train"]]
    mu=xtr.mean(0)
    sd=xtr.std(0).clamp(min=1e-3)
    model=model_for("tiny_mlp",len(FEATURE_NAMES)).to("cpu")
    model.load_state_dict(load_file(str(model_file),device="cpu"))
    model.eval()
    with torch.no_grad():
        xv=((x[indices["validation"]]-mu)/sd).clamp(-6,6)
        validation=metric_torch(model(xv).softmax(-1),y[indices["validation"]])
    original=selection["validation"]["tiny_mlp"]
    if (validation["n"]!=original["n"] or
            validation["top1_hits"]!=original["top1_hits"] or
            abs(validation["brier"]-original["brier"])>2e-5 or
            abs(validation["nll"]-original["nll"])>2e-5):
        raise ContractError("ORIGINAL_CAMPAIGN_YEAR_CPU_REPLAY_MISMATCH")
    prior=class_prior([features["targets"][i] for i in indices["train"]])
    standard={"schema":"heptatrader.laya-original-price-fold13-standardizer.v1",
              "training_fold":13,"train_rows":len(indices["train"]),
              "model_weights_sha256":pins["model_weights"],
              "revised_source_sha256":pins["revised_bars"],
              "fold_plan_sha256":pins["frozen_folds"],
              "mean":mu.tolist(),"std":sd.tolist(),
              "feature_names":list(FEATURE_NAMES),
              "frozen_train_prior":prior,
              "validation_original_gpu":original,
              "validation_cpu_reproduced":validation,
              "train_only_normalization":True,
              "revised_source_not_contemporaneous":True,
              "trade_authority":False}
    standard["standardizer_id"]=digest(standard)
    return model,mu,sd,pins,standard


def outcome_list(root,plan):
    outcomes=[]
    for i in range(SLOTS):
        p=root/("outcome-%03d.json"%i)
        if p.exists():
            obj=read_json(p)
            if obj.get("slot")!=i or obj.get("plan_id")!=plan["plan_id"]:
                raise ContractError("ORIGINAL_CAMPAIGN_OUTCOME_REPLACED")
            predfile=root/("prediction-%03d.json"%i)
            pred=read_json(predfile) if predfile.exists() else None
            pred_sha=sha_file(predfile) if pred is not None else None
            verify_saved_outcome(plan,i,obj,pred,pred_sha)
            outcomes.append(obj)
    return outcomes


def run(a):
    os.umask(0o077)
    if (not a.evidence.is_absolute() or a.evidence.is_symlink() or
            (not a.resume and a.evidence.exists()) or
            (a.resume and not a.evidence.is_dir())):
        raise ContractError("ORIGINAL_CAMPAIGN_NEW_OR_RESUME_DIRECTORY")
    if not a.resume:
        a.evidence.mkdir(parents=True,mode=0o700,exist_ok=False)
    import fcntl
    fd=os.open(a.evidence/"campaign.lock",os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    c=read_json(a.config)
    model,mu,sd,pins,standard=prepare_assets(a,c)
    standard_path=a.evidence/"standardizer.json"
    if a.resume:
        prior_standard=read_json(standard_path)
        if prior_standard!=standard or prior_standard["standardizer_id"]!=standard["standardizer_id"]:
            raise ContractError("ORIGINAL_CAMPAIGN_STANDARDIZER_CHANGED")
    else:
        write_once(standard_path,standard)
    if a.resume:
        plan=check_plan(read_json(a.evidence/"plan.json"))
        if (plan["pinned_sources"]!=pins or
                plan["train_only_normalization_sha256"]!=sha_file(standard_path)):
            raise ContractError("ORIGINAL_CAMPAIGN_PIN_CHANGED")
    else:
        plan=create_plan(int(time.time()*1000),pins,standard["frozen_train_prior"],sha_file(standard_path))
        write_once(a.evidence/"plan.json",plan)
    print("REAL_SOURCE_MLP_90_REGISTERED",json.dumps({
          "plan_id":plan["plan_id"],"start_ms":plan["start_ms"],
          "end_ms":plan["last_outcome_end_ms"],"model_sha256":pins["model_weights"],
          "no_trading":True}),flush=True)
    # Validate the WHOLE saved file set before scheduling any work.
    # STOP/restart preserves committed prediction bytes and must not fill
    # previously missed original windows in retrospect.
    outcome_list(a.evidence,plan)
    predicted={i for i in range(SLOTS) if
               (a.evidence/("prediction-%03d.json"%i)).is_file()}
    recorded={i for i in range(SLOTS) if
              (a.evidence/("outcome-%03d.json"%i)).is_file()}
    failed_reads={}
    s=state_summary(plan,outcome_list(a.evidence,plan))
    s.update(at_ms=int(time.time()*1000),last_slot=max(recorded,default=None),
             state="RUNNING",model_promotion=False,
             pending_forecasts=len(predicted-recorded),
             scheduler="NONBLOCKING_DUE_INPUT_FIRST_V1")
    write_status(a.evidence/"status.json",s)

    # The old sequential scheduler waited for slot N's outcome at the
    # exact time slot N+1's input was opening (spacing==horizon). Waiting
    # for a full endpoint grace could consume the NEXT input's only
    # opportunity. Poll independent due inputs and due outcomes, with new
    # precommit always processed first. No extra threads or trade authority.
    while not STOP and len(recorded)<SLOTS:
        now=int(time.time()*1000)
        actions=pending_actions(plan,now,predicted,recorded)
        progress=False
        for slot,action in actions:
            if STOP:break
            anchor=expected_anchor(plan,slot)
            output=a.evidence/("outcome-%03d.json"%slot)
            predfile=a.evidence/("prediction-%03d.json"%slot)

            if action=="CENSOR_INPUT":
                if predfile.exists():
                    raise ContractError("ORIGINAL_CAMPAIGN_ORPHAN_INPUT_PREDICTION")
                outcome={"schema":"heptatrader.laya-original-price-outcome.v1",
                    "plan_id":plan["plan_id"],"slot":slot,
                    "anchor_ms":anchor,"at_ms":int(time.time()*1000),
                    "status":"CENSORED",
                    "reason":"MISSED_PRECOMMITTED_INPUT_SLOT:"+failed_reads.get(slot,"INPUT_NOT_PRECOMMITTED"),
                    "automatic_trading":False,"broker_orders_sent":0,
                    "model_promotion":False}
                write_once(output,outcome)
                recorded.add(slot);progress=True
                continue

            if action=="TRY_PREDICT":
                if int(time.time()*1000)<anchor+3000:continue
                try:
                    asof=int(time.time()*1000)
                    feature,quotes,prefixes=asof_feature(c,asof,anchor)
                    if feature["anchor_ms"]!=anchor:
                        raise ContractError("ORIGINAL_CAMPAIGN_ANCHOR_CHANGED")
                    bars,_receipts=original_closed_bars(quotes,asof)
                    if not bars or bars[-1]["time"]*1000!=anchor-HORIZON_MS:
                        raise ContractError("ORIGINAL_CAMPAIGN_BASE_BAR_MISSING")
                    import torch
                    v=torch.tensor(feature["features"],dtype=torch.float32)
                    scaled=(v-mu)/sd
                    with torch.no_grad():
                        probs=model(scaled.clamp(-6,6).reshape(1,-1)).softmax(-1)[0].tolist()
                    created=int(time.time()*1000)
                    if created>anchor+INPUT_GRACE_MS:
                        raise ContractError("ORIGINAL_CAMPAIGN_INPUT_EXPIRED_BEFORE_SEAL")
                    pred=prediction(plan,slot,feature,probs,created,
                        bars[-1]["midpoint"]["close"],digest(prefixes),
                        int((scaled.abs()>6).sum()))
                    write_once(predfile,pred)
                    predicted.add(slot);progress=True
                    failed_reads.pop(slot,None)
                    print("REAL_SOURCE_MLP_PREDICTION_SEALED",json.dumps({
                      "slot":slot,"anchor_ms":anchor,"created_at_ms":created,
                      "prediction_id":pred["prediction_id"],
                      "clip_count":pred["clip_count"]}),flush=True)
                except (ContractError,OSError,ValueError,KeyError,IndexError) as exc:
                    failed_reads[slot]=type(exc).__name__+":"+str(exc)[:120]
                continue

            if slot not in predicted or not predfile.exists():
                raise ContractError("ORIGINAL_CAMPAIGN_PREDICTION_SCHEDULER_DRIFT")
            pred=read_json(predfile)
            pred_sha=sha_file(predfile)

            if action=="CENSOR_OUTCOME":
                outcome={"schema":"heptatrader.laya-original-price-outcome.v1",
                    "plan_id":plan["plan_id"],"slot":slot,"anchor_ms":anchor,
                    "at_ms":int(time.time()*1000),
                    "prediction_id":pred["prediction_id"],
                    "prediction_sha256":pred_sha,"status":"CENSORED",
                    "reason":"OUTCOME_COMMITTED_DEADLINE_MISSED:"+failed_reads.get(slot,"NO_COMPLETE_ORIGINAL_RECEIPT"),
                    "automatic_trading":False,"broker_orders_sent":0,
                    "model_promotion":False}
                verify_saved_outcome(plan,slot,outcome,pred,pred_sha)
                write_once(output,outcome)
                recorded.add(slot);progress=True
                continue

            if action!="TRY_GRADE":
                raise ContractError("ORIGINAL_CAMPAIGN_UNDECLARED_SCHEDULER_ACTION")
            try:
                outcome=poll_due_outcome_once(c,plan,slot,pred)
                if outcome is None:continue
                outcome["prediction_sha256"]=pred_sha
                verify_saved_outcome(plan,slot,outcome,pred,pred_sha)
                write_once(output,outcome)
                recorded.add(slot);progress=True
            except (ContractError,OSError,ValueError,KeyError,IndexError) as exc:
                failed_reads[slot]=type(exc).__name__+":"+str(exc)[:120]

        if progress:
            s=state_summary(plan,outcome_list(a.evidence,plan))
            s.update(at_ms=int(time.time()*1000),
                     last_slot=max(recorded,default=None),state="RUNNING",
                     pending_forecasts=len(predicted-recorded),
                     model_promotion=False,scheduler="NONBLOCKING_DUE_INPUT_FIRST_V1")
            write_status(a.evidence/"status.json",s)
            print("REAL_SOURCE_MLP_PROGRESS",json.dumps({
                "observed":s["observed"],"censored":s["censored"],
                "recorded":s["recorded"],"open_forecasts":len(predicted-recorded)}),flush=True)
        if not STOP and len(recorded)<SLOTS:
            time.sleep(.5)

    s=state_summary(plan,outcome_list(a.evidence,plan))
    final="STOPPED_RECOVERABLE" if STOP else (
       "ORIGINAL_SOURCE_90_COVERAGE_PASSED_ONLY" if s["observed"]>=88 and s["censored"]<=2
       else "ORIGINAL_SOURCE_90_COVERAGE_FAILED")
    s.update(state=final,at_ms=int(time.time()*1000),model_promotion=False)
    write_status(a.evidence/"status.json",s)
    print("REAL_SOURCE_MLP_90_END",json.dumps(s),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",required=True,type=Path)
    parser.add_argument("--bars",required=True,type=Path)
    parser.add_argument("--folds",required=True,type=Path)
    parser.add_argument("--year-evidence",required=True,type=Path)
    parser.add_argument("--evidence",required=True,type=Path)
    parser.add_argument("--resume",action="store_true")
    a=parser.parse_args()
    run(a)


if __name__=="__main__":
    main()
