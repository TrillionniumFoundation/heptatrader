#!/usr/bin/env python3
"""14-fold revised-history EURUSD 5m seven-class price-head GPU experiment.

Research only. No original Value mutation, execution service, order permission
or production Laya model enrollment. Source is revised, NOT contemporaneous.
"""
from __future__ import annotations
import argparse
import copy
from collections import Counter
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"research/neuron_learning")]
from laya_cell.price_year_gpu import (features_from_bars,fold_indices,class_prior,
    winner,FEATURE_NAMES,FEATURE_VERSION,SCHEMA,LR,DECAY,EPOCHS,BATCH_SIZE,PATIENT_EPOCHS)
from laya_cell.integrity import ContractError, read_json,sha_file,digest
from laya_cell.resident_store import write_once


def metric_torch(p,y):
    import torch
    from torch.nn import functional as F
    if p.ndim!=2 or p.shape[1]!=7 or y.ndim!=1 or p.shape[0]!=y.shape[0]:
        raise ContractError("YEAR_GPU_SHAPE")
    if not torch.isfinite(p).all() or not torch.isfinite(y.float()).all():
        raise ContractError("YEAR_GPU_NONFINITE")
    n=int(y.shape[0])
    return {"n":n,"brier":float(((p-F.one_hot(y,7))**2).sum(dim=1).mean()),
            "nll":float(-torch.log(p.gather(-1,y[:,None]).clamp(min=1e-12)).mean()),
            "top1_hits":int((p.argmax(dim=-1)==y).sum()),
            "top1_accuracy":float((p.argmax(dim=-1)==y).float().mean()),
            "not_trading_or_efficacy_claim":True}


def model_for(name,d):
    import torch
    from torch import nn
    if name=="linear":return nn.Linear(d,7)
    if name=="tiny_mlp":return nn.Sequential(nn.Linear(d,32),nn.Tanh(),nn.Linear(32,7))
    raise ContractError("YEAR_GPU_UNKNOWN_MODEL_FAMILY")


def train_model(name,xtr,ytr,xval,yval,dev,seed):
    import torch
    from torch.nn import functional as F
    torch.manual_seed(seed)
    m=model_for(name,xtr.shape[1]).to(dev)
    optimizer=torch.optim.AdamW(m.parameters(),lr=LR,weight_decay=DECAY)
    best=None
    best_brier=float("inf")
    best_epoch=None
    stale=0
    trained_steps=0
    for epoch in range(EPOCHS):
        m.train()
        # Fixed ordering seeded before first epoch; only training labels
        # used for gradients. Validation controls early stopping only.
        inds=torch.randperm(xtr.shape[0],device=dev)
        for j in range(0,len(inds),BATCH_SIZE):
            batch=inds[j:j+BATCH_SIZE]
            optimizer.zero_grad(set_to_none=True)
            logits=m(xtr[batch])
            loss=F.cross_entropy(logits,ytr[batch])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(m.parameters(),1.,error_if_nonfinite=True)
            optimizer.step()
            trained_steps+=1
        m.eval()
        with torch.no_grad():
            p=m(xval).softmax(-1)
            b=metric_torch(p,yval)["brier"]
        if b < best_brier - 1e-7:
            best_brier=b
            best=copy.deepcopy({k:v.detach().cpu().contiguous() for k,v in m.state_dict().items()})
            best_epoch=epoch+1
            stale=0
        else:
            stale+=1
            if stale>=PATIENT_EPOCHS:break
    if not best:
        raise ContractError("YEAR_GPU_NO_VALIDATION_CHECKPOINT")
    m.load_state_dict(best)
    m.eval()
    return m,best,{"trained_epochs":epoch+1,"selected_epoch":best_epoch,
                   "optimizer_steps":trained_steps,
                   "best_validation_brier":best_brier}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars",required=True,type=Path)
    parser.add_argument("--original-folds",required=True,type=Path)
    parser.add_argument("--trainer-guard",required=True,type=Path)
    parser.add_argument("--evidence",required=True,type=Path)
    parser.add_argument("--limit-folds",type=int,default=14)
    a=parser.parse_args()
    os.umask(0o077)
    if a.limit_folds not in (2,4,14) or a.evidence.exists() or not a.evidence.is_absolute():
        parser.error("new evidence dir and declared 2/4/14-fold limit required")
    a.evidence.mkdir(mode=0o700,parents=True)
    started=time.monotonic()
    report={"schema":SCHEMA,"at_ms":int(time.time()*1000),
            "source_revised_not_contemporaneous":True,"causal_horizon_sec":300,
            "frozen_existing_laya_candidate_replaced":False,
            "model_promotion_performed":False,"broker_orders_sent":0,
            "automatic_trading":False,"requested_folds":a.limit_folds,
            "feature_version":FEATURE_VERSION,
            "source_sha256":sha_file(a.bars),
            "folds_spec_sha256":sha_file(a.original_folds),
            "method":{"models":["linear","tiny_mlp"],"epochs_max":EPOCHS,
                      "lr":LR,"weight_decay":DECAY,"batch_size":BATCH_SIZE,
                      "early_stop_validation_patience":PATIENT_EPOCHS,
                      "standardize_only_training":True,
                      "fixed_900s_embargo":True,
                      "test_fold_results_never_used_for_selection":True}}
    try:
        import torch
        from safetensors.torch import save_file
        from historical_training.resident_resource_guard import Gate,Lease,verify_config
        torch.set_num_threads(2)
        if (not torch.version.hip or not torch.cuda.is_available() or
                not str(torch.cuda.get_device_properties(0).gcnArchName).startswith("gfx1150")):
            raise ContractError("YEAR_EXPERIMENT_POCKET4_GFX1150_REQUIRED")
        dev=torch.device("cuda:0")
        fold_plan=read_json(a.original_folds)
        source=read_json(a.bars,32*1024**2)
        if (fold_plan["input_sha256"]["bars"]!=report["source_sha256"] or
                source.get("research_only_revised_not_contemporaneous") is not True or
                source.get("trade_authority") is not False or
                fold_plan.get("tested_folds")!=14 or
                len(fold_plan.get("folds",[]))!=14 or
                source.get("schema")!="heptatrader.historical-paired-bars.v1"):
            raise ContractError("YEAR_SOURCE_OR_EXISTING_FOLD_IDENTITY")
        data=features_from_bars(source["bars"])
        report["examples_all"]=len(data["features"])
        report["examples_omitted_due_to_gaps"]=data["omitted_due_to_gaps"]
        report["source_bar_count"]=len(source["bars"])
        report["feature_names"]=list(FEATURE_NAMES)
        # Full research matrix on GPU, without touching the neural price head.
        x=torch.tensor(data["features"],device=dev,dtype=torch.float32)
        y=torch.tensor(data["targets"],device=dev,dtype=torch.long)
        if x.shape[1]!=len(FEATURE_NAMES):
            raise ContractError("YEAR_GPU_FEATURE_DIMS")
        if not torch.isfinite(x).all():
            raise ContractError("YEAR_GPU_BAD_FEATURES")
        g=read_json(a.trainer_guard)
        original_lock=Path(g["state_root"])/"heavy-worker.lock"
        g["state_root"]=str(a.evidence/"admission")
        verify_config(g)
        gate=Gate(g,"trainer")
        gate.lease=Lease(original_lock)
        aggregate={"test_observations":0,
            "prior_brier_sum":0.,"prior_nll_sum":0.,
            "selected_brier_sum":0.,"selected_nll_sum":0.,
            "selected_top1_hits":0,"prior_top1_hits":0}
        test_anchors=set()
        results=[]
        # Record the entire baseline protocol before calculating fold labels.
        plan_receipt={"schema":"heptatrader.year-gpu-protocol-frozen.v1",
                      "original_fold_source_sha256":report["folds_spec_sha256"],
                      "historical_bar_sha256":report["source_sha256"],
                      "source_is_revised_not_contemporaneous":True,
                      "fold_count":a.limit_folds,"time_900s_embargo":True,
                      "fixed_horizon_sec":300,
                      "runner_source_sha256":sha_file(ROOT/"scripts/research_laya_price_year_gpu.py"),
                      "feature_source_sha256":sha_file(ROOT/"research/neuron_learning/laya_cell/price_year_gpu.py"),
                      "family_list":["prior","linear","tiny_mlp"],
                      "normalization":"TRAIN_ONLY_MEAN_STD_CLAMP_6",
                      "validation_selection":"MIN_BRIER_ONLY_IF_NLL_NOT_WORSE_THAN_PRIOR",
                      "never_trading":True}
        plan_receipt["protocol_id"]=digest(plan_receipt)
        write_once(a.evidence/"protocol.json",plan_receipt)
        report["protocol_id"]=plan_receipt["protocol_id"]
        for fold in fold_plan["folds"][:a.limit_folds]:
            fold_number=fold["fold"]
            inds=fold_indices(data,fold)
            # Test fold rows cannot recur in another frozen test fold,
            # regardless of train/validation overlap in historical replay.
            actual={data["anchor_ms"][i] for i in inds["test"]}
            if not test_anchors.isdisjoint(actual):
                raise ContractError("YEAR_REPLAY_TEST_DUPLICATED_BETWEEN_FOLDS")
            test_anchors.update(actual)
            acquired=False
            for attempt in range(90):
                if gate.acquire({"fold":fold_number,"purpose":"REVISED_PRICE_5M_GPU_BACKTEST"}):
                    acquired=True
                    break
                if attempt%10==0:
                    print("VALUE_PRIORITY_WAIT",fold_number,gate.last_admission.get("reasons"),flush=True)
                time.sleep(2)
            if not acquired:
                raise ContractError("YEAR_VALUE_PRIORITY_OR_HEAVY_WORKER_UNAVAILABLE")
            try:
                itrain=torch.tensor(inds["train"],device=dev)
                ival=torch.tensor(inds["validation"],device=dev)
                itest=torch.tensor(inds["test"],device=dev)
                xtr=x[itrain];ytr=y[itrain]
                xval=x[ival];yval=y[ival]
                xte=x[itest];yte=y[itest]
                mu=xtr.mean(0);std=xtr.std(0).clamp(min=1e-3)
                xtr=((xtr-mu)/std).clamp(-6,6)
                xval=((xval-mu)/std).clamp(-6,6)
                xte=((xte-mu)/std).clamp(-6,6)
                train_classes=[data["targets"][i] for i in inds["train"]]
                weights=torch.tensor(class_prior(train_classes),device=dev,dtype=torch.float32)
                prior_val=metric_torch(weights.expand(len(ival),-1),yval)
                validation={"prior":prior_val}
                models={}
                training_receipts={}
                for name,seed in (("linear",20261016+fold_number*2),
                                  ("tiny_mlp",20261017+fold_number*2)):
                    model,best,train_receipt=train_model(name,xtr,ytr,xval,yval,dev,seed)
                    with torch.no_grad():
                        validation[name]=metric_torch(model(xval).softmax(-1),yval)
                    path=a.evidence/("fold-%02d-%s.safetensors"%(fold_number,name))
                    save_file(best,str(path))
                    path.chmod(0o400)
                    train_receipt["adapter_sha256"]=sha_file(path)
                    training_receipts[name]=train_receipt
                    models[name]=model
                selected=winner(validation)
                receipt={"schema":"heptatrader.year-validation-selection.v1",
                         "fold":fold_number,"train_start":fold["train_start_utc"],
                         "train_end":fold["train_end_utc"],
                         "val_start":fold["val_start_utc"],
                         "val_end":fold["val_end_utc"],
                         "test_start":fold["test_start_utc"],
                         "test_end":fold["test_end_utc"],
                         "counts":{k:len(v) for k,v in inds.items()},
                         "validation":validation,
                         "selected_before_test":selected,
                         "test_data_used_for_training_or_selection":False,
                         "training":training_receipts,
                         "trade_authority":False}
                write_once(a.evidence/("fold-%02d-selection.json"%fold_number),receipt)
                with torch.no_grad():
                    test_scores={"prior":metric_torch(weights.expand(len(itest),-1),yte)}
                    for name,model in models.items():
                        test_scores[name]=metric_torch(model(xte).softmax(-1),yte)
                choice=test_scores[selected]
                base=test_scores["prior"]
                n=len(itest)
                aggregate["test_observations"]+=n
                aggregate["prior_brier_sum"]+=n*base["brier"]
                aggregate["prior_nll_sum"]+=n*base["nll"]
                aggregate["selected_brier_sum"]+=n*choice["brier"]
                aggregate["selected_nll_sum"]+=n*choice["nll"]
                aggregate["selected_top1_hits"]+=choice["top1_hits"]
                aggregate["prior_top1_hits"]+=base["top1_hits"]
                part={"fold":fold_number,"validation_selected":selected,
                      "train_rows":len(itrain),"validation_rows":len(ival),
                      "test_rows":n,"test_scores":test_scores,
                      "test_compared_after_validation_frozen":True,
                      "no_trading":True}
                write_once(a.evidence/("fold-%02d-test.json"%fold_number),part)
                results.append(part)
                print("FOLD_DONE",fold_number,"SELECTED",selected,"TEST_BRIER",
                      round(choice["brier"],6),"PRIOR",round(base["brier"],6),
                      "NLL",round(choice["nll"],6),flush=True)
            finally:
                gate.release()
        n=aggregate["test_observations"]
        report.update(state="COMPLETED_GPU_RESEARCH_NO_PROMOTION",
            folds_completed=len(results),per_fold=results,
            aggregate={"test_n":n,
                       "prior_brier":aggregate["prior_brier_sum"]/n,
                       "prior_nll":aggregate["prior_nll_sum"]/n,
                       "selected_brier":aggregate["selected_brier_sum"]/n,
                       "selected_nll":aggregate["selected_nll_sum"]/n,
                       "selected_top1_hits":aggregate["selected_top1_hits"],
                       "prior_top1_hits":aggregate["prior_top1_hits"],
                       "selected_families":dict(Counter(r["validation_selected"] for r in results)),
                       "unique_test_anchors":len(test_anchors),
                       "source_revised_not_contemporaneous":True,
                       "neural_production_model_fitted":False,
                       "financial_alpha_proven":False,
                       "statistical_prospective_qualification":False},
            gpu_arch=str(torch.cuda.get_device_properties(0).gcnArchName),
            gpu_peak_bytes=torch.cuda.max_memory_allocated())
    except Exception as exc:
        report.update(state="INCOMPLETE_OR_FAILED_GPU_RESEARCH",error_class=type(exc).__name__,
                      error=str(exc)[:400])
    finally:
        report["wall_seconds"]=round(time.monotonic()-started,3)
        report["ended_at_ms"]=int(time.time()*1000)
        write_once(a.evidence/"result.json",report)
        print("ANNUAL_RESULT",json.dumps({k:report.get(k) for k in
            ("state","folds_completed","error_class","error","wall_seconds","aggregate")},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
