#!/usr/bin/env python3
"""Pocket4-only bounded diagnosis; frozen historical rows, no deployment."""
from pathlib import Path
from collections import Counter
from bisect import bisect_right
import argparse,hashlib,json,os,sys,time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"research/neuron_learning"))
from laya_cell import price_readout_diagnostics as d
from laya_cell.dataset import BIN_EDGES_BPS,question

SOURCE_SHA="3d701e3795cb438b5ba38f3bb7c44bed83003e040108d1378d1084635157e4a9"
CUTOFF=1791504000000
HOLD_END=1791590400000

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def write(p,v):
    data=(json.dumps(v,sort_keys=True,allow_nan=False,separators=(",",":"))+"\n").encode()
    with p.open("xb") as f:f.write(data);f.flush();os.fsync(f.fileno())
    p.chmod(0o400)
    return sha(p)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",type=Path,required=True);p.add_argument("--evaluation-root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    if Path("/etc/hostname").read_text().strip()!="alex-G1628-04":raise RuntimeError("POCKET4_ONLY")
    if sha(a.source)!=SOURCE_SHA:raise RuntimeError("FROZEN_SOURCE_CHANGED")
    a.output.mkdir(parents=True,mode=0o700,exist_ok=False)
    protocol={"schema":"heptatrader.price-readout-diagnostic-protocol.v1","at_ms":int(time.time()*1000),
        "source_sha256":SOURCE_SHA,"train_cutoff_ms":CUTOFF,"holdout_end_ms":HOLD_END,
        "arms":["parent","train_frequency","intercept_only","temperature_only"],
        "lambda":"1/N_TRAIN","frequency_smoothing":.5,"bias_max_newton_steps":50,
        "bias_gradient_tolerance":1e-8,"inverse_temperature_bounds":[0.,10.],
        "holdout_previously_seen":True,"no_holdout_selection":True,
        "code_sha256":sha(Path(d.__file__)),"runner_sha256":sha(Path(__file__)),
        "price_bins_bps":list(BIN_EDGES_BPS),"broker_orders_sent":0,"model_promoted":False}
    protocol_sha=write(a.output/"protocol.json",protocol)
    source=json.loads(a.source.read_text());rows=source["rows"]
    if len(rows)!=298 or len({r["row_id"] for r in rows})!=298:raise RuntimeError("ROW_IDENTITY")
    train=sorted([r for r in rows if r["anchor_ms"]<CUTOFF],key=lambda r:(r["anchor_ms"],r["row_id"]))
    hold=sorted([r for r in rows if CUTOFF<=r["anchor_ms"]<HOLD_END],key=lambda r:(r["anchor_ms"],r["row_id"]))
    if (len(train),len(hold))!=(178,120) or any(r["future_outcome_available_ms"]>CUTOFF for r in train):
        raise RuntimeError("FROZEN_TIME_SPLIT_CHANGED")
    plans={}
    for pf in list(a.evaluation_root.glob("*/plan.json"))+list(a.evaluation_root.glob("*/*/plan.json")):
        v=json.loads(pf.read_text());plans[v["plan_id"]]=pf.parent
    label_audit=Counter()
    for r in rows:
        directory=plans[r["plan_id"]];of=directory/("outcome-%03d.json"%r["slot"])
        pf=directory/("prediction-%03d.json"%r["slot"])
        if sha(of)!=r["outcome_sha256"] or sha(pf)!=r["prediction_sha256"]:raise RuntimeError("ORIGINAL_RECEIPT_CHANGED")
        outcome=json.loads(of.read_text())
        if (outcome["target_class"]!=r["target_class"] or bisect_right(BIN_EDGES_BPS,outcome["return_bps"])!=r["target_class"]
                or outcome["outcome_receipt"]!=r["original_outcome_receipt"]
                or r["question"]!=question(r["state"]["horizon_sec"])):
            raise RuntimeError("LABEL_BIN_OR_PROMPT_MISMATCH")
        label_audit[str(r["state"]["horizon_sec"])]+=1
    tp=[r["frozen_parent_probs"] for r in train];ty=[r["target_class"] for r in train]
    hp=[r["frozen_parent_probs"] for r in hold];hy=[r["target_class"] for r in hold]
    started=time.monotonic();bias=d.fit_bias(tp,ty);temp=d.fit_temperature(tp,ty);freq=d.fit_frequency(ty)
    parent=d.metrics(hp,hy)
    if abs(parent["nll"]-2.361973169527537)>1e-6 or abs(parent["brier"]-.9908813852929451)>1e-6:
        raise RuntimeError("ORIGINAL_PARENT_SCORE_REPRODUCTION")
    hp_freq=np.broadcast_to(freq["probabilities"],(len(hy),7))
    scores={"parent":parent,"train_frequency":d.metrics(hp_freq,hy),
        "intercept_only":d.metrics(hp,hy,bias=bias["bias"]),
        "temperature_only":d.metrics(hp,hy,inverse_temperature=temp["inverse_temperature"])}
    result={"schema":"heptatrader.price-readout-diagnostic.v1","state":"COMPLETED_RESEARCH_ONLY",
        "at_ms":int(time.time()*1000),"protocol_sha256":protocol_sha,"source_sha256":SOURCE_SHA,
        "train_rows_digest":digest([r["row_id"] for r in train]),"holdout_rows_digest":digest([r["row_id"] for r in hold]),
        "original_label_hash_and_bin_checks":len(rows),"label_mismatches":0,"horizon_seconds_counts":dict(label_audit),
        "train_rows":len(train),"holdout_rows":len(hold),"bias_fit":bias,"temperature_fit":temp,
        "frequency_fit":freq,"holdout_scores":scores,"elapsed_fit_seconds":time.monotonic()-started,
        "actual_optimizer_updates":bias["applied_newton_steps"],"gpu_passes":0,"model_forward_passes":0,
        "live_model_unchanged":True,"holdout_previously_seen":True,"new_prospective_qualification":False,
        "broker_orders_sent":0,"policy_gradient_steps":0,"model_promoted":False}
    h=write(a.output/"result.json",result)
    print(json.dumps({"path":str(a.output/"result.json"),"sha256":h,"label_checks":len(rows),
        "horizon_counts":dict(label_audit),"bias_steps":bias["applied_newton_steps"],
        "bias":bias["bias"],"inverse_temperature":temp["inverse_temperature"],
        "elapsed_seconds":result["elapsed_fit_seconds"],
        "holdout":{k:{x:v[x] for x in ["rows","nll","brier","accuracy","predicted_class_counts"]} for k,v in scores.items()}}))

if __name__=="__main__":main()
