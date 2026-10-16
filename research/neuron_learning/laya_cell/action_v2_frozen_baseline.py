"""Historical, original-receipt-bound ACT correctness baseline; never an order policy.

ONE fixed train/test split only: outcomes known by 2026-10-09T00:00Z are
eligible for fitting, 2026-10-09 anchors are holdout. This is NOT future
prospective validation, V2 LoRA training or a trade authorization.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from .integrity import ContractError, digest
from .resident_action_v2_closed_source import check_train_source

TRAIN_CUTOFF_MS = 1791504000000  # 2026-10-09T00:00:00Z
HOLD_END_MS = 1791590400000     # 2026-10-10T00:00:00Z
EPOCHS = 300
LR = 0.05
L2 = 0.1
PROB_MIN = 1e-12
SCHEMA = "heptatrader.laya-action-v2-closed-confidence-baseline.v1"


def _row(row):
    if (not isinstance(row, dict) or type(row.get("anchor_ms")) is not int
            or type(row.get("future_outcome_available_ms")) is not int
            or row["future_outcome_available_ms"] <= row["anchor_ms"]
            or type(row.get("parent_correct")) is not bool
            or type(row.get("target_class")) is not int
            or not 0 <= row["target_class"] < 7
            or type(row.get("row_id")) is not str or len(row["row_id"]) != 64
            or type(row.get("plan_id")) is not str
            or type(row.get("slot")) is not int):
        raise ContractError("ACTION_BASELINE_ORIGINAL_ROW_IDENTITY")
    p = row.get("frozen_parent_probs")
    if (type(p) is not list or len(p) != 7
            or any(type(v) not in (float, int) or not math.isfinite(v)
                   or v < 0 or v > 1 for v in p)
            or abs(sum(p)-1) > 1e-5):
        raise ContractError("ACTION_BASELINE_FROZEN_PROBABILITIES")
    parent_choice = max(range(7), key=lambda i: p[i])
    if (parent_choice == row["target_class"]) is not row["parent_correct"]:
        raise ContractError("ACTION_BASELINE_ORIGINAL_TARGET_CONFLICT")
    return max(p)


def partition(rows):
    if type(rows) is not list or not rows or len(rows) > 4096:
        raise ContractError("ACTION_BASELINE_ROW_POPULATION")
    seen = set()
    train, hold = [], []
    for row in rows:
        conf = _row(row)
        identity = (row["plan_id"], row["slot"])
        if (identity in seen or row["row_id"] in seen):
            raise ContractError("ACTION_BASELINE_DUPLICATE_ROW")
        seen.add(identity)
        seen.add(row["row_id"])
        if row["anchor_ms"] < TRAIN_CUTOFF_MS:
            if row["future_outcome_available_ms"] > TRAIN_CUTOFF_MS:
                # An old anchor with a late-arriving label is unavailable.
                continue
            train.append((conf, int(row["parent_correct"])))
        elif row["anchor_ms"] < HOLD_END_MS:
            hold.append((conf, int(row["parent_correct"])))
        else:
            raise ContractError("ACTION_BASELINE_UNEXPECTED_LATER_COHORT")
    if not train or not hold or min(sum(y for _, y in train),
                                     sum(1-y for _, y in train)) < 4:
        raise ContractError("ACTION_BASELINE_TRAIN_TWO_CLASS_MINIMUM")
    if not any(y for _, y in hold) or not any(1-y for _, y in hold):
        raise ContractError("ACTION_BASELINE_HOLDOUT_ONE_CLASS")
    return train, hold


def sigmoid(z):
    if z >= 0:
        return 1.0/(1.0+math.exp(-z))
    e = math.exp(z)
    return e/(1.0+e)


def fit(train):
    """Deterministic CPU full-batch logistic fitting; NO class reweighting."""
    n = len(train)
    mean = sum(x for x, _ in train)/n
    scale = max(1e-6, math.sqrt(sum((x-mean)**2 for x, _ in train)/n))
    positive = sum(y for _, y in train)
    prior = (positive + .5)/(n+1)   # train-only Jeffreys smoothing
    intercept = math.log(prior/(1-prior))
    slope = 0.0
    for _ in range(EPOCHS):
        gi = 0.0
        gs = 0.0
        for x, y in train:
            v = max(-10.0, min(10.0, (x-mean)/scale))
            error = sigmoid(intercept+slope*v)-y
            gi += error
            gs += error*v
        intercept -= LR * gi/n
        slope -= LR * (gs/n+L2*slope)
    return {"intercept": intercept, "slope": slope, "normalizer_mean": mean,
            "normalizer_scale": scale, "training_prior_correct": prior,
            "optimizer_steps": EPOCHS, "learning_rate": LR, "l2_slope": L2,
            "trainable_parameters": 2, "training_examples": n,
            "training_positive": positive}


def predict(x, model):
    z = max(-10.0, min(10.0, (x-model["normalizer_mean"])/model["normalizer_scale"]))
    return sigmoid(model["intercept"]+model["slope"]*z)


def score(rows, method, model):
    n = len(rows)
    if method == "train_prior":
        probs = [model["training_prior_correct"] for _ in rows]
    elif method == "frozen_parent_confidence":
        probs = [x for x, _ in rows]
    elif method == "cpu_train_only_logistic":
        probs = [predict(x, model) for x, _ in rows]
    else:
        raise ContractError("ACTION_BASELINE_UNKNOWN_METHOD")
    brier = sum(2*(p-y)**2 for p, (_, y) in zip(probs, rows))/n
    nll = -sum(y*math.log(max(PROB_MIN,min(1-PROB_MIN,p)))
               +(1-y)*math.log(max(PROB_MIN,min(1-PROB_MIN,1-p)))
               for p, (_, y) in zip(probs, rows))/n
    return {"total":n, "correct_parent_ACT":sum(y for _,y in rows),
            "binary_brier":brier, "binary_nll":nll,
            "ACT_suggestions_at_5_of_6":sum(p >= 5/6 for p in probs),
            "model_only_research": True}


def analyze(artifact, source_sha256):
    m = check_train_source(artifact)
    train, hold = partition(artifact["rows"])
    model = fit(train)
    result = {"schema": SCHEMA, "frozen_parent_id":m["frozen_parent_id"],
              "original_source_sha256": source_sha256,
              "original_manifest_id":m["manifest_id"],
              "source_rows_digest":m["rows_digest"],
              "fixed_train_cutoff_ms":TRAIN_CUTOFF_MS,
              "fixed_holdout_end_ms":HOLD_END_MS,
              "train_counts": {"n":len(train), "ACT":sum(y for _,y in train)},
              "holdout_counts": {"n":len(hold), "ACT":sum(y for _,y in hold)},
              "fitted_cpu_control":model,
              "train_scores":{method:score(train,method,model) for method in
                       ("train_prior","frozen_parent_confidence","cpu_train_only_logistic")},
              "holdout_scores":{method:score(hold,method,model) for method in
                       ("train_prior","frozen_parent_confidence","cpu_train_only_logistic")},
              "model_is_not_fitted_on_holdout":True,
              "historical_already_seen_not_prospective":True,
              "not_full_action_v2_lora_training":True,
              "no_broker_calls":True,"model_promoted":False,
              "trading_authority":False}
    result["report_id"]=digest(result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--expected-source-sha256",required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    if (not args.source.is_absolute() or not args.output.is_absolute()
            or not args.output.parent.is_dir()
            or args.output.parent.resolve()==args.source.parent.resolve()
            or len(args.expected_source_sha256)!=64):
        p.error("private pinned source and separate existing output directory required")
    fd=os.open(args.source,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,"rb") as f:
        s=os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_size > 4*1024*1024 or s.st_size == 0:
            raise ContractError("ACTION_BASELINE_SOURCE_FILE_SCOPE")
        body=f.read(4*1024*1024+1)
    if len(body)!=s.st_size or hashlib.sha256(body).hexdigest()!=args.expected_source_sha256:
        raise ContractError("ACTION_BASELINE_SOURCE_SHA_CHANGED")
    def unique_pairs(pairs):
        d={}
        for k,v in pairs:
            if k in d:raise ContractError("ACTION_BASELINE_DUPLICATE_JSON_KEY")
            d[k]=v
        return d
    artifact=json.loads(body,object_pairs_hook=unique_pairs)
    result=analyze(artifact,args.expected_source_sha256)
    result["program_sha256"]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["receipt_digest"]=digest(result)
    os.umask(0o077)
    outfd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
    with os.fdopen(outfd,"w",encoding="utf-8") as f:
        json.dump(result,f,sort_keys=True,separators=(",",":"),allow_nan=False)
        f.write("\n");f.flush();os.fsync(f.fileno())
    dirfd=os.open(args.output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    print(json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
