"""Independent recomputation of frozen price-only Ridge weights, no trading."""
from __future__ import annotations
import argparse, hashlib, json, os, time
from pathlib import Path
import numpy as np
from historical_training.common import read, sha, digest
from historical_training.eurusd_walkforward_cost import features,regression

def run(bars_path,model_path,plan_path,reg_path,baseline_path):
    bars=read(Path(bars_path),64*1024*1024)
    model=read(Path(model_path),1024*1024)
    plan=read(Path(plan_path))
    registration=read(Path(reg_path))
    baseline=read(Path(baseline_path))
    assert sha(Path(bars_path))==model["historical_bars_sha256"],"HISTORICAL_DATA_MOVED"
    assert baseline["tested_folds"]==4
    assert all(f["selected_validation_threshold_bps"] is None for f in baseline["folds"]), "PREVIOUS_FAILURE_RELABELED"
    assert model["historical_validation_selected_action"]=="NO_TRADE"
    assert model["historical_holdout_previously_seen"] is True
    assert model["trade_authority"] is False
    assert plan["broker_orders_sent"]==0 and plan["promotion_allowed"] is False
    assert registration["plan_id"]==plan["plan_id"] and registration["trade_authority"] is False
    s=features(bars["bars"])
    mean,sd,w=regression(s["x"],s["returns"])
    for k,val in (("mean",mean),("stddev",sd),("weights",w)):
        assert np.allclose(np.array(model[k]),val,rtol=0,atol=1e-12), "FROZEN_WEIGHT_REPRODUCTION_CHANGED"
    assert int(s["mature"].max()*1000)==model["last_matured_historical_target_ms"]
    assert len(s["x"])==model["trained_rows"]==41243
    assert model["last_matured_historical_target_ms"]<plan["first_decision_ms"]
    assert registration["sealed_at_ms"]<plan["first_decision_ms"]-30000
    return {"schema":"heptatrader.fx-ridge-independent-frozen-model-verify.v1",
        "verified_at_ms":int(time.time()*1000),
        "model_digest":digest(model),"research_only":True,
        "fitted_historical_rows":model["trained_rows"],
        "trained_model_recomputed_exact_numpy_tolerance":1e-12,
        "original_old_fold_count":4,"old_fold_trade_thresholds":[None]*4,
        "new_frozen_model_not_an_OOS_performance_claim":True,
        "prospective_plan_id":plan["plan_id"],
        "source_revised_historical":True,
        "broker_requests_sent":0,"broker_orders_sent":0,
        "trade_authority":False,"model_promotion_allowed":False,
        "state":"FROZEN_MODEL_RECOMPUTED_PREVIOUS_NO_TRADE_VALIDATION_PRESERVED"}

def main():
    p=argparse.ArgumentParser()
    for x in ("bars","model","plan","registration","baseline","output"):
        p.add_argument("--"+x,required=True,type=Path)
    a=p.parse_args();os.umask(0o077)
    report=run(a.bars,a.model,a.plan,a.registration,a.baseline)
    fd=os.open(a.output,os.O_EXCL|os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as fh:
        json.dump(report,fh,indent=2,sort_keys=True);fh.write("\n")
        fh.flush();os.fsync(fh.fileno())
    print(json.dumps({k:report[k] for k in ("state","fitted_historical_rows","broker_orders_sent")}))
if __name__=="__main__":main()
