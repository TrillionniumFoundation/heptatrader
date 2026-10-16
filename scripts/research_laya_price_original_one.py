#!/usr/bin/env python3
"""One frozen original-source 5m price forecast; read-only Pocket4 research.

This is NOT the 90-slot original neural qualification, NOT a trader, and
NOT IB HMDS exact-bar equivalence. It never mutates trial history or weights.
"""
from __future__ import annotations
import argparse
from bisect import bisect_right
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research/neuron_learning"))
from laya_cell.integrity import ContractError, read_json,sha_file,digest
from laya_cell.resident_store import write_once
from laya_cell.resident_recent_source import read_recent_original_quotes
from laya_cell.price_original_live_features import original_feature_snapshot, original_closed_bars
from laya_cell.price_year_gpu import features_from_bars,fold_indices,class_prior,FEATURE_NAMES
from laya_cell.dataset import BIN_EDGES_BPS
from research_laya_price_year_gpu import model_for


def outcome_from_new_closed_bar(bars,anchor_ms,base_mid):
    """Outcome is the close of the NEXT completed original quote bucket."""
    match=[b for b in bars if b["time"]*1000==anchor_ms]
    if len(match)!=1:
        raise ContractError("ORIGINAL_FORWARD_ENDPOINT_NOT_UNIQUELY_COMPLETE")
    x=match[0]["midpoint"]["close"]
    return {"target_class":bisect_right(BIN_EDGES_BPS,(x/base_mid-1)*10000),
            "return_bps":(x/base_mid-1)*10000,"end_mid":x}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("--bars",type=Path,required=True)
    parser.add_argument("--folds",type=Path,required=True)
    parser.add_argument("--year-evidence",type=Path,required=True)
    parser.add_argument("--evidence",type=Path,required=True)
    a=parser.parse_args()
    os.umask(0o077)
    if (not a.evidence.is_absolute() or a.evidence.exists()):
        parser.error("new absolute research directory required")
    a.evidence.mkdir(mode=0o700,parents=True)
    import torch
    from safetensors.torch import load_file
    torch.set_num_threads(2)
    protocol=read_json(a.year_evidence/"protocol.json")
    selection=read_json(a.year_evidence/"fold-13-selection.json")
    c=read_json(a.config)
    source=read_json(a.bars,32*1024**2)
    folds=read_json(a.folds)
    candidate=a.year_evidence/"fold-13-tiny_mlp.safetensors"
    pinned={'historical_bars':sha_file(a.bars),
       'folds':sha_file(a.folds),'candidate':sha_file(candidate),
       'feature_module':sha_file(ROOT/"research/neuron_learning/laya_cell/price_year_gpu.py"),
       'original_feature_module':sha_file(ROOT/"research/neuron_learning/laya_cell/price_original_live_features.py"),
       'runner':sha_file(Path(__file__))}
    if (pinned['historical_bars']!=protocol['historical_bar_sha256'] or
        pinned['folds']!=protocol['original_fold_source_sha256'] or
        pinned['candidate']!=selection['training']['tiny_mlp']['adapter_sha256'] or
        pinned['feature_module']!=protocol['feature_source_sha256'] or
        selection['selected_before_test']!="tiny_mlp" or
        source.get("research_only_revised_not_contemporaneous") is not True):
        raise ContractError("ORIGINAL_FORWARD_FROZEN_MODEL_OR_SOURCE_CHANGED")
    data=features_from_bars(source["bars"])
    plan=next(f for f in folds["folds"] if f["fold"]==13)
    inds=fold_indices(data,plan)
    x=torch.tensor(data["features"],device="cpu",dtype=torch.float32)
    y=torch.tensor(data["targets"],device="cpu",dtype=torch.long)
    xt=x[inds["train"]]
    mu=xt.mean(0)
    std=xt.std(0).clamp(min=1e-3)
    model=model_for("tiny_mlp",len(FEATURE_NAMES))
    model.load_state_dict(load_file(str(candidate),device="cpu"))
    model.eval()
    with torch.no_grad():
        xp=((x[inds["validation"]]-mu)/std).clamp(-6,6)
        pv=model(xp).softmax(-1)
        from research_laya_price_year_gpu import metric_torch
        v=metric_torch(pv,y[inds["validation"]])
    if abs(v["brier"]-selection["validation"]["tiny_mlp"]["brier"])>2e-5:
        raise ContractError("ORIGINAL_FORWARD_NORMALIZATION_REPRODUCTION")
    weights=class_prior([data["targets"][i] for i in inds["train"]])

    started=int(time.time()*1000)
    last_end=(started//300000)*300000
    # Never use an input older than 60 seconds. Bind the whole forecast
    # to the next bar-close if the current close is already stale.
    anchor=last_end if started-last_end<30000 else last_end+300000
    frozen={"schema":"heptatrader.original-quote-research-plan.v1",
      "fixed_anchor_ms":anchor,"created_ms":started,
      "horizon_ms":300000,"input_grace_ms":60000,
      "selected_fold":13,"selected_family":"tiny_mlp",
      "historical_source_revised_not_contemporaneous":True,
      "real_source_different_from_hmds_bars":True,
      "model_and_data_pins":pinned,
      "original_reader_config_sha256":sha_file(a.config),
      "historical_validation_brier_reproduced":v["brier"],
      "no_model_promotion":True,"no_trading":True,"orders_sent":0}
    frozen["plan_id"]=digest(frozen)
    write_once(a.evidence/"plan.json",frozen)
    result={"schema":"heptatrader.original-quote-research-outcome.v1",
      "plan_id":frozen["plan_id"],"requested_anchor_ms":anchor,
      "historical_validation_brier":v["brier"],
      "source_kind":"ORIGINAL_VERIFIED_POCKET4_QUOTES",
      "checkpoint_sha256":pinned["candidate"],
      "formal_trial_registered":False,"model_promoted":False,"orders_sent":0}
    try:
        target=anchor+10000
        while int(time.time()*1000)<target:
            time.sleep(min(2,max(.01,(target-int(time.time()*1000))/1000)))
        asof=int(time.time()*1000) # freeze before original source read
        quotes,prefixes=read_recent_original_quotes(c,asof)
        feature=original_feature_snapshot(quotes,prefixes,asof,require_fresh=True)
        if feature["anchor_ms"]!=anchor:
            raise ContractError("ORIGINAL_FORWARD_FIXED_ANCHOR_MISSED")
        xlive=torch.tensor(feature["features"],dtype=torch.float32)
        raw_z=(xlive-mu)/std
        with torch.no_grad():
            p=model(raw_z.clamp(-6,6)[None,:]).softmax(-1)[0].tolist()
        if not all(0<=z<=1 for z in p) or abs(sum(p)-1)>1e-5:
            raise ContractError("ORIGINAL_FORWARD_NONFINITE_MODEL_PROBS")
        bars,_=original_closed_bars(quotes,asof)
        base_mid=bars[-1]["midpoint"]["close"]
        sealed={"schema":"heptatrader.original-price-precommit.v1",
          "plan_id":frozen["plan_id"],"source_snapshot_id":feature["snapshot_id"],
          "source_prefixes_digest":feature["quote_prefixes_digest"],
          "anchor_ms":anchor,"created_at_ms":int(time.time()*1000),
          "model_probs":p,"training_prior_probs":weights,
          "model_checkpoint_sha256":pinned["candidate"],
          "last_closed_mid":base_mid,
          "source_feature_vector_sha256":digest(feature["features"]),
          "asof_ms":asof,
          "source_not_identical_to_revised_ib_bars":True,
          "clip_count":int((raw_z.abs()>6).sum()),
          "trading_authority":False,"broker_orders_sent":0}
        if sealed["created_at_ms"]>=anchor+300000:
            raise ContractError("ORIGINAL_FORWARD_AFTER_HORIZON")
        sealed["receipt_id"]=digest(sealed)
        write_once(a.evidence/"prediction.json",sealed)
        result.update(state="FUTURE_PREDICTION_SEALED",prediction_sha256=sha_file(a.evidence/"prediction.json"),
                      prediction_created_at_ms=sealed["created_at_ms"],
                      input_quote_age_ms=asof-anchor,
                      model_top1=p.index(max(p)),prior_top1=weights.index(max(weights)),
                      feature_clip_count=sealed["clip_count"])
        write_once(a.evidence/"stage-one.json",result)
        print("REAL_ORIGINAL_SOURCE_PRICE_PREDICTION_SEALED",json.dumps({
          "anchor_ms":anchor,"prediction_sha256":result["prediction_sha256"],
          "age_ms":result["input_quote_age_ms"],"source_kind":result["source_kind"]}),flush=True)

        due=anchor+300000+10000
        while int(time.time()*1000)<due:
            time.sleep(min(2,max(.01,(due-int(time.time()*1000))/1000)))
        after=int(time.time()*1000)
        q2,pins2=read_recent_original_quotes(c,after)
        end_bars,end_receipts=original_closed_bars(q2,after)
        outcome=outcome_from_new_closed_bar(end_bars,anchor,base_mid)
        target_class=outcome["target_class"]
        result.update(state="FIRST_TRUE_ORIGINAL_SOURCE_5M_OUTCOME_OBSERVED",
            future_target_class=target_class,future_return_bps=outcome["return_bps"],
            future_outcome_ms=anchor+300000,
            model_brier=sum((p[i]-(i==target_class))**2 for i in range(7)),
            prior_brier=sum((weights[i]-(i==target_class))**2 for i in range(7)),
            model_hit=int(p.index(max(p))==target_class),
            prior_hit=int(weights.index(max(weights))==target_class),
            future_source_prefixes_digest=digest(pins2),
            original_close_receipt=end_receipts[[b["time"]*1000 for b in end_bars].index(anchor)]["last_quote_receipt"],
            source_price_close_mid=outcome["end_mid"],
            not_statistical_or_financial_efficacy=True)
    except Exception as exc:
        result.update(state="FIRST_PROSPECTIVE_ORIGINAL_SOURCE_FAILED_OR_CENSORED",
            error_class=type(exc).__name__,reason=str(exc)[:360])
    finally:
        result["ended_ms"]=int(time.time()*1000)
        write_once(a.evidence/"result.json",result)
        print("FINAL_ORIGINAL_FORWARD_RESULT",json.dumps(result,sort_keys=True),flush=True)


if __name__=="__main__":main()
