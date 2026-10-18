#!/usr/bin/env python3
"""Replay a frozen seven-bin baseline on newly matured IB historical labels."""
import argparse,hashlib,json,os,sys,time
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
from safetensors.numpy import load_file
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"research/neuron_learning"))
from laya_cell import price_year_gpu as features

MODEL_SHA="7340d6429fbeb342ffd3d2038f4f3ea4b6f5252dcd6bc0efa7ef77fdd95c72e1"
STANDARDIZER_SHA="afe348acd053c03699c98c799fed4c7a1135f0f57aa57b40bc7628dde42b3f67"
FEATURE_SHA="5f9d39203365feca689e24916cd28fb4a4f94692b07405ac21f4134bbe491c14"

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
    return h.hexdigest()

def write(path,value):
    with path.open("x") as f:
        json.dump(value,f,sort_keys=True,separators=(",",":"),allow_nan=False);f.write("\n");f.flush();os.fsync(f.fileno())
    path.chmod(0o400);return sha(path)

def iso(ms):return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ["merged","model","standardizer","output"]:ap.add_argument("--"+name,type=Path,required=True)
    a=ap.parse_args()
    if Path("/etc/hostname").read_text().strip()!="alex-G1628-04":raise RuntimeError("POCKET4_ONLY")
    if sha(a.model)!=MODEL_SHA or sha(a.standardizer)!=STANDARDIZER_SHA or sha(features.__file__)!=FEATURE_SHA:
        raise RuntimeError("FROZEN_MODEL_OR_FEATURE_IDENTITY")
    proof=json.loads((a.merged/"merge-proof.json").read_text());base=Path(proof["base_path"])
    if sha(a.merged/"paired-bars.json")!=proof["merged_paired_sha256"] or sha(base/"coverage.json")!=proof["base_coverage_sha256"]:
        raise RuntimeError("MERGE_SOURCE_CHANGED")
    if sha(base/"paired-bars.json")!=proof["base_paired_sha256"]:raise RuntimeError("BASE_SOURCE_CHANGED")
    old_coverage=json.loads((base/"coverage.json").read_text())
    old_last=int(datetime.fromisoformat(old_coverage["actual_last_utc"]).timestamp())
    old_cutoff=(old_last+300)*1000
    a.output.mkdir(parents=True,mode=0o700,exist_ok=False)
    protocol={"schema":"heptatrader.increment-frozen-replay-protocol.v1","at_ms":int(time.time()*1000),
        "merged_paired_sha256":proof["merged_paired_sha256"],"merge_proof_sha256":sha(a.merged/"merge-proof.json"),
        "frozen_model_sha256":MODEL_SHA,"train_only_standardizer_sha256":STANDARDIZER_SHA,
        "feature_source_sha256":FEATURE_SHA,"horizon_seconds":300,"bins_bps":[-10,-3,-1,1,3,10],
        "old_closed_bar_cutoff_ms":old_cutoff,"selection":"TARGET_CLOSE_END_AFTER_OLD_CUTOFF",
        "model_fitted_or_selected_on_increment":False,"source_revised_not_contemporaneous":True,
        "broker_orders_sent":0,"model_promoted":False,"runner_sha256":sha(__file__)}
    ph=write(a.output/"protocol.json",protocol)
    all_bars=json.loads((a.merged/"paired-bars.json").read_text())["bars"]
    last_close=(all_bars[-1]["time"]+300)*1000
    # Include the old trailing input window so its newly matured label is kept.
    bars=[b for b in all_bars if b["time"]>=old_last-12*300]
    del all_bars
    data=features.features_from_bars(bars)
    inds=[i for i,anchor in enumerate(data["anchor_ms"]) if old_cutoff<anchor+300000<=last_close]
    if not inds:raise RuntimeError("NO_NEW_MATURE_LABELS")
    std=json.loads(a.standardizer.read_text());weights=load_file(str(a.model))
    if std["feature_names"]!=list(features.FEATURE_NAMES) or std["model_weights_sha256"]!=MODEL_SHA or std["train_only_normalization"] is not True:
        raise RuntimeError("STANDARDIZER_IDENTITY")
    if set(weights)!={"0.weight","0.bias","2.weight","2.bias"} or weights["0.weight"].shape!=(32,24) or weights["2.weight"].shape!=(7,32):
        raise RuntimeError("FROZEN_TINY_MLP_SHAPE")
    x=np.asarray([data["features"][i] for i in inds],dtype=np.float32)
    y=[data["targets"][i] for i in inds]
    mu=np.asarray(std["mean"],dtype=np.float32);scale=np.asarray(std["std"],dtype=np.float32)
    x=np.clip((x-mu)/scale,-6,6)
    logits=(np.tanh(x@weights["0.weight"].T+weights["0.bias"])@weights["2.weight"].T+weights["2.bias"]).astype(np.float64)
    logits-=logits.max(1,keepdims=True);p=np.exp(logits);p/=p.sum(1,keepdims=True)
    prior=np.broadcast_to(np.asarray(std["frozen_train_prior"]),(len(y),7))
    scores={"frozen_tiny_mlp":features.metrics(p.tolist(),y),"frozen_train_prior":features.metrics(prior.tolist(),y)}
    rows=[{"anchor_ms":data["anchor_ms"][i],"target_close_end_ms":data["anchor_ms"][i]+300000,
           "target_class":y[j],"probabilities":p[j].tolist()} for j,i in enumerate(inds)]
    predsha=write(a.output/"predictions.json",{"rows":rows,"broker_orders_sent":0})
    result={"schema":"heptatrader.increment-frozen-price-replay.v1","state":"COMPLETED_HISTORICAL_RESEARCH",
        "at_ms":int(time.time()*1000),"protocol_sha256":ph,"predictions_sha256":predsha,
        "new_paired_bars":proof["new_rows"],"newly_matured_labels":len(rows),
        "old_tail_labels_newly_matured":sum(r["anchor_ms"]<=old_cutoff for r in rows),
        "last_new_bar_without_future_label":True,"omitted_due_to_gaps":data["omitted_due_to_gaps"],
        "first_anchor_utc":iso(rows[0]["anchor_ms"]),"first_target_close_utc":iso(rows[0]["target_close_end_ms"]),
        "last_target_close_utc":iso(rows[-1]["target_close_end_ms"]),
        "true_class_counts":np.bincount(y,minlength=7).tolist(),"scores":scores,
        "same_frozen_training_rows":std["train_rows"],"new_training_steps":0,"gpu_passes":0,
        "broker_orders_sent":0,"model_promoted":False,"economic_alpha_proven":False,
        "new_prospective_qualification":False,"source_revised_not_contemporaneous":True}
    rh=write(a.output/"result.json",result)
    print(json.dumps({"path":str(a.output/"result.json"),"sha256":rh,**result}))

if __name__=="__main__":main()
