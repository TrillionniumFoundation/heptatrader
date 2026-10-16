"""Causal 300-second seven-bin features from revised 5-minute IB bars.

The historical revision flag is explicitly preserved. These are NOT original
prospectively committed quote timestamps, IB executions or real trading PnL.
"""
from __future__ import annotations
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
from math import sin,cos,pi,log,isfinite,sqrt

from .integrity import ContractError
from .dataset import BIN_EDGES_BPS

SCHEMA="heptatrader.laya-revised-5m-price-gpu.v1"
FEATURE_VERSION="12_CAUSAL_5M_RETURNS_RANGE_SPREAD_VOL_TIME_V1"
HORIZON_SECONDS=300
LOOKBACK_RETURNS=12
ALPHA=0.5
LR=0.003
DECAY=0.001
EPOCHS=12
BATCH_SIZE=1024
PATIENT_EPOCHS=3
FAMILIES=("prior","linear","tiny_mlp")
MODEL_SELECTION="VALIDATION_BRIER_AND_NLL_NOT_WORSE_VS_PRIOR"
FEATURE_NAMES=(
    *("return_bps_lag_%d" % i for i in range(12)),
    "mean_return_3","mean_return_6","mean_return_12",
    "abs_move_mean_3","abs_move_mean_6","abs_move_mean_12",
    "latest_range_bps","latest_spread_bps",
    "utc_hour_sin","utc_hour_cos","utc_weekday_sin","utc_weekday_cos")


def features_from_bars(bars):
    if not isinstance(bars,list) or len(bars)<LOOKBACK_RETURNS+2:
        raise ContractError("YEAR_BARS_INSUFFICIENT")
    mid=[];bid=[];ask=[];ranges=[];times=[]
    for bar in bars:
        t=bar.get("time")
        if type(t) is not int:
            raise ContractError("YEAR_BAR_TIME")
        try:
            b=float(bar["bid"]["close"]);a=float(bar["ask"]["close"])
            m=float(bar["midpoint"]["close"])
            hi=float(bar["midpoint"]["high"]);lo=float(bar["midpoint"]["low"])
        except (KeyError,TypeError,ValueError) as ex:
            raise ContractError("YEAR_BAR_SHAPE_OR_PRICE") from ex
        if (not all(isfinite(x) for x in (b,a,m,hi,lo)) or
                not 0<b<=a or abs(m-(a+b)*0.5)>0.00012 or
                not 0<lo<=m+0.0002 and hi>=m-0.0002 or lo>hi):
            raise ContractError("YEAR_BAR_CROSSED_OR_INCONSISTENT")
        times.append(t);bid.append(b);ask.append(a);mid.append(m);ranges.append((hi-lo)/m*10000)
    if any(u>=v for u,v in zip(times,times[1:])):
        raise ContractError("YEAR_BARS_NOT_ASCENDING")
    features=[];target=[];anchors=[];valid_gaps=Counter()
    for i in range(LOOKBACK_RETURNS,len(bars)-1):
        # The whole input lookback and the next five-minute label interval
        # must be unbroken, including weekends and market closures.
        if any(times[k]-times[k-1]!=HORIZON_SECONDS
               for k in range(i-LOOKBACK_RETURNS+1,i+2)):
            valid_gaps["LOOKBACK_OR_LABEL_GAP"]+=1;continue
        previous=[(mid[k]/mid[k-1]-1)*10000 for k in range(i-LOOKBACK_RETURNS+1,i+1)]
        if len(previous)!=LOOKBACK_RETURNS:
            raise ContractError("YEAR_FEATURE_HISTORY_LENGTH")
        d=datetime.fromtimestamp(times[i]+HORIZON_SECONDS,timezone.utc)
        hour=d.hour+d.minute/60
        x=(previous+
            [sum(previous[-k:])/k for k in (3,6,12)]+
            [sum(abs(q) for q in previous[-k:])/k for k in (3,6,12)]+
            [ranges[i],(ask[i]-bid[i])/mid[i]*10000,
             sin(2*pi*hour/24),cos(2*pi*hour/24),
             sin(2*pi*d.weekday()/7),cos(2*pi*d.weekday()/7)])
        if len(x)!=len(FEATURE_NAMES) or any(not isfinite(y) for y in x):
            raise ContractError("YEAR_FEATURE_NONFINITE")
        label=bisect_right(BIN_EDGES_BPS,(mid[i+1]/mid[i]-1)*10000)
        features.append(x);target.append(label);anchors.append((times[i]+300)*1000)
    return {"features":features,"targets":target,"anchor_ms":anchors,
            "input_known_only_after_bar_close":True,
            "label_horizon_ms":300000,
            "original_revised_data_not_contemporaneous":True,
            "omitted_due_to_gaps":sum(valid_gaps.values())}


def _ms(value):
    d=datetime.fromisoformat(value.replace("Z","+00:00"))
    if d.tzinfo is None:raise ContractError("YEAR_FOLD_NAIVE_TIME")
    return int(d.timestamp()*1000)


def fold_indices(dataset,fold):
    a=dataset["anchor_ms"]
    windows={}
    for role,prefix,start_name,end_name in (
        ("train","train","train_start_utc","train_end_utc"),
        ("validation","val","val_start_utc","val_end_utc"),
        ("test","test","test_start_utc","test_end_utc")):
        start=_ms(fold[start_name]);end=_ms(fold[end_name])
        # Record the anchor, not just the original historical bar opening.
        # Labels must have matured before the train/validation/test cutoff.
        values=[i for i,t in enumerate(a) if start<=t and t+300000<=end]
        if not values:raise ContractError("YEAR_FOLD_EMPTY_"+role)
        windows[role]=values
    train_end=_ms(fold["train_end_utc"])
    val_start=_ms(fold["val_start_utc"])
    val_end=_ms(fold["val_end_utc"])
    test_start=_ms(fold["test_start_utc"])
    if not (train_end+900000<=val_start and val_end+900000<=test_start):
        raise ContractError("YEAR_FOLD_NO_900S_EMBARGO")
    if (max(a[i]+300000 for i in windows["train"])>train_end or
            min(a[i] for i in windows["validation"])<val_start or
            max(a[i]+300000 for i in windows["validation"])>val_end or
            min(a[i] for i in windows["test"])<test_start):
        raise ContractError("YEAR_FOLD_TARGET_LEAKAGE")
    return windows


def class_prior(labels):
    if len(labels)<16 or any(type(y) is not int or not 0<=y<7 for y in labels):
        raise ContractError("YEAR_CLASS_PRIOR_INPUT")
    counts=Counter(labels);n=len(labels)+7*ALPHA
    return [(counts[i]+ALPHA)/n for i in range(7)]


def metrics(probs,labels):
    if len(probs)!=len(labels) or not labels:
        raise ContractError("YEAR_METRICS_LENGTH")
    b=nll=0.
    correct=0
    for p,y in zip(probs,labels):
        if (not isinstance(p,(list,tuple)) or len(p)!=7 or
                any(type(x) not in (float,int) or not isfinite(x) or not 0<=x<=1 for x in p) or
                abs(sum(p)-1)>1e-5 or type(y) is not int or not 0<=y<7):
            raise ContractError("YEAR_METRICS_PROBABILITY")
        b+=sum((p[i]-int(i==y))**2 for i in range(7))
        nll-=log(max(p[y],1e-12))
        correct+=int(max(range(7),key=lambda i:p[i])==y)
    return {"n":len(labels),"brier":b/len(labels),"nll":nll/len(labels),
            "top1_correct":correct,"top1_accuracy":correct/len(labels),
            "not_economic_or_prospective_qualification":True}


def winner(scores):
    if set(scores)!=set(FAMILIES):raise ContractError("YEAR_UNDECLARED_FAMILY")
    reference=scores["prior"]
    admissible=[name for name in FAMILIES
                if scores[name]["brier"]<=reference["brier"]
                and scores[name]["nll"]<=reference["nll"]]
    return min(admissible,key=lambda name:(scores[name]["brier"],FAMILIES.index(name)))
