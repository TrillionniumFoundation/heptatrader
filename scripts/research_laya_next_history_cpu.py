#!/usr/bin/env python3
"""One bounded CPU rolling-fold refresh on Pocket4 revised IB history."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import argparse,json,hashlib,os,sys,time
import numpy as np
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/"research/neuron_learning"),str(Path(__file__).resolve().parent)]
from laya_cell import price_year_gpu as f
from research_laya_price_year_gpu import train_model,metric_torch
from research_laya_history_increment_replay import sha,write,FEATURE_SHA

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ["bars","original-folds","dataset-output","output"]:p.add_argument("--"+n,type=Path,required=True)
    a=p.parse_args()
    if Path("/etc/hostname").read_text().strip()!="alex-G1628-04" or sha(f.__file__)!=FEATURE_SHA:
        raise RuntimeError("POCKET4_FROZEN_FEATURE_SOURCE_REQUIRED")
    if sha(a.bars)!="1eb91d41bbfb6fc9fd3b9e25f0f866766bcb5a9553c3e807c95ee8851cc1281a":
        raise RuntimeError("VERIFIED_INCREMENT_DATASET_REQUIRED")
    original=json.loads(a.original_folds.read_text())
    if sha(a.original_folds)!="08f0a649da6054706452b1a8c5df388e6c3a7381bdf1f9cd66e31bd802008e41":
        raise RuntimeError("ORIGINAL_FOLD_PROTOCOL_CHANGED")
    last=original["folds"][-1]
    fold={"fold":last["fold"]+1}
    for key in ("train_start_utc","train_end_utc","val_start_utc","val_end_utc","test_start_utc","test_end_utc"):
        fold[key]=(datetime.fromisoformat(last[key])+timedelta(days=15)).isoformat()
    a.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    protocol={"schema":"heptatrader.next-rolling-price-cpu-protocol.v1","at_ms":int(time.time()*1000),
        "fold":fold,"historical_bars_sha256":sha(a.bars),"feature_source_sha256":FEATURE_SHA,
        "training_source_sha256":sha(Path(__file__).with_name("research_laya_price_year_gpu.py")),
        "runner_sha256":sha(__file__),"horizon_seconds":300,"classes":7,
        "families":["prior","linear","tiny_mlp"],"epochs_max":f.EPOCHS,"batch_size":f.BATCH_SIZE,
        "learning_rate":f.LR,"weight_decay":f.DECAY,"early_stop_validation_patience":f.PATIENT_EPOCHS,
        "model_selection":f.MODEL_SELECTION,"test_scope":"MATURED_PARTIAL_NEXT_FOLD_ALREADY_REVISED",
        "gpu_permitted":False,"broker_orders_sent":0,"model_promoted":False}
    protocolsha=write(a.output/"protocol.json",protocol)
    source=json.loads(a.bars.read_text());data=f.features_from_bars(source["bars"])
    analysis_end=(source["bars"][-1]["time"]+300)*1000;del source
    a.dataset_output.mkdir(mode=0o700,parents=True,exist_ok=False)
    npz=a.dataset_output/"features-targets.npz"
    with npz.open("xb") as h:
        np.savez_compressed(h,features=np.asarray(data["features"],dtype=np.float32),
            targets=np.asarray(data["targets"],dtype=np.int64),anchor_ms=np.asarray(data["anchor_ms"],dtype=np.int64))
        h.flush();os.fsync(h.fileno())
    npz.chmod(0o400)
    contract={"schema":"heptatrader.sevenbin-five-minute-historical-dataset.v1",
        "bars_sha256":protocol["historical_bars_sha256"],"array_sha256":sha(npz),"feature_source_sha256":FEATURE_SHA,
        "rows":len(data["targets"]),"features":list(f.FEATURE_NAMES),"bins_bps":[-10,-3,-1,1,3,10],
        "bin_rule":"bisect_right","horizon_seconds":300,"anchor":"INPUT_BAR_CLOSE",
        "target":"NEXT_BAR_CLOSE_OVER_INPUT_BAR_CLOSE_MINUS_ONE_TIMES_10000",
        "outcome_known_at":"anchor_ms+300000","omitted_due_to_gaps":data["omitted_due_to_gaps"],
        "revised_not_contemporaneous":True,"actual_execution_rewards":0,"broker_orders_sent":0,"trade_authority":False}
    contractsha=write(a.dataset_output/"dataset-contract.json",contract)
    inds=f.fold_indices(data,fold)
    import torch
    from safetensors.torch import save_file
    torch.set_num_threads(1);dev=torch.device("cpu")
    if torch.cuda.is_initialized():raise RuntimeError("GPU_INITIALIZED")
    x=torch.tensor(data["features"],dtype=torch.float32);y=torch.tensor(data["targets"],dtype=torch.long)
    tr,va,te=(torch.tensor(inds[k],dtype=torch.long) for k in ("train","validation","test"))
    xtr=x[tr];ytr=y[tr];mu=xtr.mean(0);std=xtr.std(0).clamp(min=1e-3)
    xtr=((xtr-mu)/std).clamp(-6,6);xv=((x[va]-mu)/std).clamp(-6,6);xt=((x[te]-mu)/std).clamp(-6,6)
    prior=torch.tensor(f.class_prior([data["targets"][i] for i in inds["train"]]),dtype=torch.float32)
    validation={"prior":metric_torch(prior.expand(len(va),-1),y[va])};models={};receipts={}
    started=time.monotonic()
    for name,seed in [("linear",20261016+fold["fold"]*2),("tiny_mlp",20261017+fold["fold"]*2)]:
        model,best,receipt=train_model(name,xtr,ytr,xv,y[va],dev,seed)
        with torch.no_grad():validation[name]=metric_torch(model(xv).softmax(-1),y[va])
        path=a.output/(name+".safetensors");save_file(best,str(path));path.chmod(0o400)
        receipts[name]={**receipt,"seed":seed,"weights_sha256":sha(path),
            "trainable_parameters":sum(p.numel() for p in model.parameters())};models[name]=model
    selected=f.winner(validation)
    selection={"selected_before_test":selected,"validation":validation,"training":receipts,
        "counts":{k:len(v) for k,v in inds.items()},"protocol_sha256":protocolsha,
        "dataset_contract_sha256":contractsha,"normalizer":{"mean":mu.tolist(),"std":std.tolist(),"clip":6,
            "train_rows":len(tr),"train_only":True},"frozen_prior":prior.tolist(),"test_used_in_selection":False}
    selectionsha=write(a.output/"selection.json",selection)
    with torch.no_grad():
        scores={"prior":metric_torch(prior.expand(len(te),-1),y[te])}
        scores.update({name:metric_torch(model(xt).softmax(-1),y[te]) for name,model in models.items()})
    if torch.cuda.is_initialized():raise RuntimeError("GPU_INITIALIZED")
    result={"schema":"heptatrader.next-rolling-price-cpu-result.v1","at_ms":int(time.time()*1000),
        "state":"COMPLETED_PARTIAL_HISTORICAL_TEST_RESEARCH_ONLY","protocol_sha256":protocolsha,
        "selection_sha256":selectionsha,"dataset_contract_sha256":contractsha,"fold":fold,
        "counts":selection["counts"],"selected_before_test":selected,"validation_scores":validation,
        "test_scores":scores,"training":receipts,"actual_total_optimizer_steps":sum(r["optimizer_steps"] for r in receipts.values()),
        "elapsed_training_seconds":time.monotonic()-started,"analysis_end_utc":datetime.fromtimestamp(analysis_end/1000,timezone.utc).isoformat(),
        "planned_test_interval_complete":False,"test_holdout_previously_seen_in_research":True,
        "gpu_initialized":False,"live_model_replaced":False,"model_promoted":False,"broker_orders_sent":0,
        "economic_alpha_proven":False,"new_prospective_qualification":False}
    h=write(a.output/"result.json",result)
    print(json.dumps({"path":str(a.output/"result.json"),"sha256":h,"sevenbin_dataset_rows":contract["rows"],**result}))

if __name__=="__main__":main()
