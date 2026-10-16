#!/usr/bin/env python3
"""Pocket4 ONLY: experimental V2 ACT LoRA real-parent probe and bounded fit.

No broker client, policy selector, model promotion, or write to production paths.
A *real* pinned Guard acquire (including VALUE/source/memory and shared flock)
is required before any GPU model load. The complete original source is read-only.
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

SOURCE=Path("/home/alex/.local/state/heptatrader/operator-evidence/laya-original-quote-asof-forensics-20261016/action-v2-real-closed-parent298-trainonly.json")
SOURCE_SHA="3d701e3795cb438b5ba38f3bb7c44bed83003e040108d1378d1084635157e4a9"
GUARD_CONFIG=Path("/home/alex/.local/state/heptatrader/operator-evidence/flow-repair-20261016/trainer-guard.json")
TRAIN_CUTOFF_MS=1791504000000
HOLDOUT_END_MS=1791590400000
MAX_GUARD_SECONDS=530
BATCH=4
MAX_PAIR_STEPS=8
ACTION_LR=.0001
SCHEMA="heptatrader.action-v2-pocket4-real-gpu-isolation.v1"


def source_bytes():
    fd=os.open(SOURCE,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,"rb") as f:
        s=os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or not 0<s.st_size<=4*1024*1024:
            raise RuntimeError("SOURCE_TYPE_OR_SIZE")
        body=f.read(4*1024*1024+1)
    if len(body)!=s.st_size or hashlib.sha256(body).hexdigest()!=SOURCE_SHA:
        raise RuntimeError("SOURCE_SHA_CHANGED")
    return body


def strict_pairs(values):
    out={}
    for k,v in values:
        if k in out:raise RuntimeError("DUPLICATE_SOURCE_JSON_KEY")
        out[k]=v
    return out


def partition(source):
    from laya_cell.resident_action_v2_closed_source import check_train_source
    m=check_train_source(source)
    rows=source["rows"]
    train=[];hold=[];excluded=[]
    for r in rows:
        if r["parent_id"]!=m["frozen_parent_id"]:
            raise RuntimeError("FROZEN_PARENT_ROW_MISMATCH")
        if r["anchor_ms"]<TRAIN_CUTOFF_MS:
            if r["future_outcome_available_ms"]<=TRAIN_CUTOFF_MS:train.append(r)
            else:excluded.append(r["row_id"])
        elif r["anchor_ms"]<HOLDOUT_END_MS:hold.append(r)
        else:raise RuntimeError("UNEXPECTED_FUTURE_COHORT")
    if (len(rows)!=298 or len(train)!=178 or len(hold)!=120 or excluded
            or sum(r["parent_correct"] for r in train)!=9
            or sum(r["parent_correct"] for r in hold)!=5
            or len({r["row_id"] for r in rows})!=298):
        raise RuntimeError("FROZEN_SPLIT_OR_LABEL_COUNTS_CHANGED")
    return m,train,hold


def score(values, labels):
    if len(values)!=len(labels) or not values:raise RuntimeError("SCORE_DENOMINATOR")
    if any(v is None for v in values):
        return {"total":len(labels),"graded":0,"unscorable_ood":sum(v is None for v in values),
                "binary_brier":None,"nll":None,"act_at_5_of_6":0}
    if any(not math.isfinite(float(p)) or not 0<=p<=1 for p in values):
        raise RuntimeError("PROBABILITY_NONFINITE")
    n=len(labels)
    return {"total":n,"graded":n,"unscorable_ood":0,
            "positive":sum(labels),
            "binary_brier":2*sum((p-y)**2 for p,y in zip(values,labels))/n,
            "nll":-sum(y*math.log(max(1e-12,min(1-1e-12,p)))+
                  (1-y)*math.log(max(1e-12,min(1-1e-12,1-p)))
                  for p,y in zip(values,labels))/n,
            "act_at_5_of_6":sum(p>=5/6 for p in values)}


def write_once(path, value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
    data=(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
    with os.fdopen(fd,"wb") as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    return hashlib.sha256(data).hexdigest()


def deadline(start):
    if time.monotonic()-start>MAX_GUARD_SECONDS:
        raise RuntimeError("TRAINING_GUARD_MAX_HOLD_EXCEEDED")


def check_sealed_parent_prediction(row, probabilities):
    """Never fit original ACT labels when real original parent is unreplayable."""
    expected=row["frozen_parent_probs"]
    if (len(probabilities)!=7 or len(expected)!=7
            or any(not math.isfinite(v) for v in probabilities)
            or abs(sum(probabilities)-1)>1e-5):
        raise RuntimeError("ORIGINAL_REPLAY_INVALID_PROBABILITY_VECTOR")
    err=max(abs(x-y) for x,y in zip(probabilities,expected))
    if err>5e-5:
        raise RuntimeError("ORIGINAL_SEALED_PRICE_PROBABILITY_PARITY_FAILED")
    if ((max(range(7),key=lambda i: probabilities[i])==row["target_class"])
            is not row["parent_correct"]):
        raise RuntimeError("ORIGINAL_PARENT_CORRECTNESS_PARITY_FAILED")
    return err


def model_forward(cell, tokenizer, rows, start, *, stage, expected=True):
    """Original real GPU forwards; no gradient, no per-row private output."""
    import torch
    from laya_cell.dataset import encode_rows
    action=[];price=[];worst=0.
    cell.eval()
    with torch.no_grad():
        for offset in range(0,len(rows),BATCH):
            deadline(start)
            selected=rows[offset:offset+BATCH]
            b={k:v.to("cuda:0") for k,v in encode_rows(tokenizer,selected).items()}
            logits,actions=cell(**b)
            p=logits.softmax(-1).detach().cpu().tolist()
            a=actions.detach().cpu()
            if not torch.isfinite(actions).all() or not torch.isfinite(logits).all():
                raise RuntimeError("NONFINITE_ORIGINAL_FORWARD")
            for r,pp,aa in zip(selected,p,a.tolist()):
                if expected:
                    worst=max(worst,check_sealed_parent_prediction(r,pp))
                price.append(pp)
                action.append(aa)
            if (offset+BATCH)%32==0 or offset+BATCH>=len(rows):
                print("FORWARD",stage,min(offset+BATCH,len(rows)),len(rows),
                      "s",round(time.monotonic()-start,2),flush=True)
    return action,price,worst


def run(mode,outdir):
    import torch
    from safetensors.torch import load_file,save_file
    from torch.nn import functional as F
    from historical_training.resident_resource_guard import verify_config,Gate
    from laya_cell.integrity import read_json,digest,sha_file
    from laya_cell.model import load_verified_laya
    from laya_cell.resident_model import ResidentDecisionCell
    from laya_cell.resident_train import validate_parent
    from laya_cell.dataset import encode_rows
    from laya_cell.resident_action_v2_research import (
        decide_train_only_action, balanced_action_loss, v2_action_inference)
    from laya_cell.action_v2_temporal_pairing import time_matched_action_pairs

    body=source_bytes()
    source=json.loads(body,object_pairs_hook=strict_pairs)
    manifest,train,hold=partition(source)
    c=read_json(GUARD_CONFIG)
    original=verify_config(c)
    if original.get("automatic_trading") is not False:raise RuntimeError("BROKER_AUTHORITY_UNEXPECTED")
    gate=Gate(c,"trainer")
    observed_at_ms=int(time.time()*1000)
    if not gate.acquire({"role":"research_action_v2_isolated","mode":mode,
                         "source_sha256":SOURCE_SHA,"orders":0}):
        report={"schema":SCHEMA,"mode":mode,"status":"NO_GPU_ADMISSION",
                "observed_at_ms":observed_at_ms,
                "guard_denial_reasons":gate.last_admission.get("reasons",[]),
                "original_sha256":SOURCE_SHA,"model_promoted":False,
                "broker_orders_sent":0}
        print("NO_GPU_ADMISSION",report["guard_denial_reasons"],flush=True)
        write_once(outdir/"denied.json",report)
        return 3
    start=time.monotonic()
    report={"schema":SCHEMA,"mode":mode,"status":"IN_PROGRESS",
            "program_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "observed_at_ms":observed_at_ms,
            "original_source_sha256":SOURCE_SHA,
            "original_manifest_id":manifest["manifest_id"],
            "training_cutoff_utc":"2026-10-09T00:00:00Z",
            "training_rows":len(train),"training_true_act":9,
            "historical_holdout_rows":len(hold),"historical_holdout_true_act":5,
            "parent_id":manifest["frozen_parent_id"],"paired_action_only":True,
            "actual_optimizer_steps":0,
            "model_promoted":False,"broker_orders_sent":0,
            "trade_authority":False,
            "historical_holdout_not_prospective":True}
    try:
        torch.manual_seed(20261007)
        base=Path(original["base_directory"])
        parent=Path(original["initial_parent_directory"])
        pm=validate_parent(parent)
        if pm["candidate_id"]!=manifest["frozen_parent_id"]:
            raise RuntimeError("FROZEN_PARENT_MANIFEST_ID_CHANGED")
        raw,tok,base_id=load_verified_laya(base,read_json(base/"base-manifest.json"))
        if base_id!=pm["base_id"]:raise RuntimeError("BASE_ID_CHANGED")
        cell=ResidentDecisionCell(raw,base_id)
        cell.restore_parent(load_file(str(parent/"adapter.safetensors")),
                            allow_scorer_only=True)
        # All base weights and scorer adapters are frozen. Only the *new*
        # action LoRA A/B may receive a gradient in the isolated candidate.
        for name,p in cell.named_parameters():
            p.requires_grad_(name in ("model.act_head.2.A","model.act_head.2.B"))
        trainables=[p for p in cell.parameters() if p.requires_grad]
        if len(trainables)!=2:
            raise RuntimeError("ACTION_ONLY_PARAMETER_MASK_CHANGED")
        cell.eval()
        torch.cuda.reset_peak_memory_stats()
        print("PARENT_LOADED",base_id,"s",round(time.monotonic()-start,2),flush=True)

        if mode=="pilot":
            sample=[train[0],train[1],hold[0]]
            a,p,err=model_forward(cell,tok,sample,start,stage="pilot",expected=True)
            report.update({"status":"PASS","parent_action_gap_samples":
                [float(z[0]-z[1]) for z in a],
                "max_sealed_price_error":err,"optimizer_steps":0,
                "peak_gpu_allocated_bytes":torch.cuda.max_memory_allocated(),
                "elapsed_seconds":time.monotonic()-start})
            cell.assert_frozen()
            write_once(outdir/"pilot-result.json",report)
            return 0
        if mode!="fit":raise RuntimeError("UNRECOGNIZED_RUN_MODE")

        parent_train,parent_price_train,e_train=model_forward(
            cell,tok,train,start,stage="train-parent",expected=True)
        # Seal V2 train-only temperature before reading ANY Oct9 holdout
        # action logits. Original true labels remain frozen by receipt.
        profile=decide_train_only_action(train,cutoff_ms=TRAIN_CUTOFF_MS,
            parent_action_logits=torch.tensor(parent_train,dtype=torch.float64))
        pairs=time_matched_action_pairs(train,profile)
        if len(pairs["pairs"])!=MAX_PAIR_STEPS or not pairs["eligible"]:
            raise RuntimeError("FROZEN_REAL_TRAIN_PAIRING_CHANGED")
        profile_id=profile["profile_id"]
        report["frozen_v2_profile_id"]=profile_id
        report["frozen_train_temperature"]=profile["action_temperature_training_only"]
        report["train_paired_steps"]=len(pairs["pairs"])
        print("TRAIN_PROFILE",profile_id,"pairs",len(pairs["pairs"]),
              "temp",profile["action_temperature_training_only"],flush=True)
        parent_hold,parent_price_hold,e_hold=model_forward(
            cell,tok,hold,start,stage="hold-parent",expected=True)
        if max(e_train,e_hold)>5e-5:
            raise RuntimeError("FROZEN_ORIGINAL_PRICE_PARITY")
        p0=v2_action_inference(torch.tensor(parent_hold,dtype=torch.float64),profile)
        hold_labels=[int(r["parent_correct"]) for r in hold]
        prior=(9+.5)/(178+1)
        report["parent_holdout"]=score(p0["action_correct_probs"],hold_labels)
        report["train_prior_holdout"]=score([prior]*len(hold),hold_labels)
        report["parent_price_parity_max_error"]=max(e_train,e_hold)

        # Same frozen scorer/pretrained backbone, 8 disjoint 1+1 genuine
        # temporal-paired minibatches. No resampling, synthetic labels,
        # future outcomes, or other price-head optimizer.
        before={n:p.detach().cpu().clone() for n,p in cell.named_parameters()
                if p.requires_grad}
        scores_before={n:p.detach().cpu().clone() for n,p in cell.named_parameters()
                       if ".scorer." in n}
        optimizer=torch.optim.AdamW(trainables,lr=ACTION_LR,weight_decay=0.)
        cell.train(True)
        step_records=[]
        for ai,bi in pairs["pairs"]:
            deadline(start)
            if (train[ai]["parent_correct"] is not True or
                    train[bi]["parent_correct"] is not False):
                raise RuntimeError("ACTION_PAIR_LABEL_CHANGED")
            sample=[train[ai],train[bi]]
            batch={k:v.to("cuda:0") for k,v in encode_rows(tok,sample).items()}
            true_price=torch.tensor([r["target_class"] for r in sample],
                                    device="cuda:0")
            correctness=torch.tensor([True,False],device="cuda:0")
            optimizer.zero_grad(set_to_none=True)
            p,a=cell(**batch)
            # Parent PRICE logits remain frozen. CE supplies a genuine
            # real-price numerical reference for the action-loss cap.
            primary=F.cross_entropy(p.detach(),true_price)
            loss=balanced_action_loss(a,correctness,primary,profile)
            if not torch.isfinite(loss) or not loss.item()>0:
                raise RuntimeError("V2_NONPOSITIVE_OR_NONFINITE_LOSS")
            loss.backward()
            norm=torch.nn.utils.clip_grad_norm_(trainables,1.,error_if_nonfinite=True)
            optimizer.step()
            if not cell.norm_report()["within_profile"]:
                raise RuntimeError("V2_ACTION_ADAPTER_NUMERICAL_NORM_LIMIT")
            step_records.append({"step":len(step_records)+1,"action_loss":float(loss.detach()),
                                 "primary_price_reference":float(primary),
                                 "gradient_norm":float(norm)})
            report["actual_optimizer_steps"]=len(step_records)
            print("REAL_PAIR_GRADIENT",len(step_records),
                  "loss",round(float(loss),6),"s",round(time.monotonic()-start,2),
                  flush=True)
        cell.eval()
        if len(step_records)!=MAX_PAIR_STEPS:
            raise RuntimeError("INCOMPLETE_REAL_PAIR_GRADIENTS")
        if any(not torch.equal(p.detach().cpu(),scores_before[n])
               for n,p in cell.named_parameters() if n in scores_before):
            raise RuntimeError("FROZEN_PRICE_HEAD_CHANGED")
        cell.assert_frozen()
        # Recompute ALL heldout forward logits under actual fitted adapter,
        # without changing sealed PRICE predictions or outcome records.
        after_action,after_price,_=model_forward(
            cell,tok,hold,start,stage="hold-fitted",expected=True)
        for left,right in zip(parent_price_hold,after_price):
            if max(abs(x-y) for x,y in zip(left,right))>1e-7:
                raise RuntimeError("ACTION_FIT_CHANGED_PRICE_OUTPUT")
        fitted=v2_action_inference(torch.tensor(after_action,dtype=torch.float64),
                                  profile)
        report["fitted_holdout"]=score(fitted["action_correct_probs"],hold_labels)
        report["ood_holdout"]=fitted["ood_abstentions"]
        report["original_action_logits_mean_gap"]=sum(a[0]-a[1] for a in
                                              parent_train)/len(parent_train)
        report["fitted_action_logits_mean_gap_holdout"]=sum(a[0]-a[1] for a in
                                              after_action)/len(after_action)
        action_changes={n:int(torch.count_nonzero(p.detach().cpu()!=before[n]))
                        for n,p in cell.named_parameters() if n in before}
        report["changed_action_coordinates"]=action_changes
        report["actual_optimizer_steps"]=len(step_records)
        report["step_records"]=step_records
        report["action_adapters"]=sorted(before)
        report["max_sealed_price_error"]=max(e_train,e_hold)
        report["price_head_unchanged"]=True
        report["pretrained_unchanged"]=True
        report["peak_gpu_allocated_bytes"]=torch.cuda.max_memory_allocated()
        report["elapsed_seconds"]=time.monotonic()-start
        report["status"]="PASS"
        if not all(n>0 for n in action_changes.values()):
            # A single LoRA factor may stay exactly initialized; deny an
            # inflated claim of full learned adapter if that happens.
            report["status"]="NO_COMPLETE_ADAPTER_COORDINATE_UPDATE"
        artifact=outdir/"fitted-action-only.safetensors"
        if artifact.exists():raise RuntimeError("OUTPUT_ALREADY_EXISTS")
        tensor_state={n:p.detach().cpu().contiguous() for n,p in
                      cell.named_parameters() if n in before}
        save_file(tensor_state,str(artifact))
        os.chmod(artifact,0o400)
        report["research_adapter_sha256"]=sha_file(artifact)
        print("FIT_END",report["status"],report["fitted_holdout"],
              "seconds",round(report["elapsed_seconds"],2),flush=True)
        write_once(outdir/"fit-result.json",report)
        return 0 if report["status"]=="PASS" else 2
    except Exception as exc:
        report["status"]="FAILED"
        report["failure_type"]=type(exc).__name__
        report["failure_code"]=str(exc)[:160]
        report["elapsed_seconds"]=time.monotonic()-start
        try:write_once(outdir/"failure.json",report)
        except OSError:pass
        print("ISOLATED_FAILURE",report["failure_code"],flush=True)
        return 2
    finally:
        gate.release()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode",choices=("pilot","fit"),required=True)
    p.add_argument("--output-directory",type=Path,required=True)
    args=p.parse_args()
    out=args.output_directory
    if (not out.is_absolute() or out.exists() or out.parent.is_symlink() or
            not out.parent.is_dir() or out.parent.resolve()!=Path(
                "/home/alex/.local/state/heptatrader/operator-evidence").resolve()):
        p.error("new Pocket4-only private evidence directory required")
    os.umask(0o077)
    out.mkdir(mode=0o700)
    result=run(args.mode,out)
    dirfd=os.open(out,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    raise SystemExit(result)


if __name__=="__main__":main()
