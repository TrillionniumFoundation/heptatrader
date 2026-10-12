"""Frozen price-only Ridge -> locally chained original EUR/USD IB PAPER 5m research.

No broker client, order, cancellation, strategy mutation, production ledger,
canonical owner or model promotion. A locally preregistered future prediction
is a research claim, not an executable trading signal or native signed proof.
"""
from __future__ import annotations
import argparse, hashlib, json, math, os, stat, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from historical_training.common import read, sha, digest
from historical_training.eurusd_walkforward_cost import features, regression, predict, verify_inputs
from .adapters import make_spec
from .causal_ttl import sealed_original_broker_expiry
from .orchestrator import register_plan, verify_plan
from .contracts import feature_spec_id
from .registry import register_before_window, verify_registration, MIN_LEAD_MS
from .original_policy_audit import write_once

SCHEMA="heptatrader.fx-ridge-original-research.v1"
PERIOD="EUR.USD"
HIST_REF="EURUSD_REVISED_5MIN_12BAR_RIDGE50_PRICE_ONLY_V1"
CODE_SHA=lambda: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
HORIZON_MS=900000
LOOKBACK=13
QUOTE_CLOSE_MAX_AGE_MS=5000
SOURCE_MAX_SEGMENTS=6
MAX_DECISION_LATENCY_MS=4500
class ResearchHold(ValueError):pass

def require(ok,reason):
    if not ok:raise ResearchHold(reason)

def raw_sha(path):
    return sha(Path(path))

def scope(config_path, cost_path):
    # Input file is an immutable private config and exact current PAPER period.
    config=read(Path(config_path))
    require(config.get("schema")=="heptatrader.laya-resident-config.v1"
            and config.get("automatic_trading") is False,"ORIGINAL_SOURCE_SCOPE")
    period_path=Path(config["period_path"])
    period=read(period_path)
    period_sha=raw_sha(period_path)
    require(config["period_sha256"]==period_sha
            and period.get("execution_domain")=="PAPER","PAPER_PERIOD_CHANGED")
    source=Path(config["feed_directory"])
    require(source.is_absolute() and not source.is_symlink()
            and source.is_dir() and stat.S_IMODE(source.stat().st_mode)==0o700,
            "REGISTERED_FEED_ROOT_INVALID")
    cost=read(cost_path)
    verify_inputs_dummy=cost.get("schema")=="heptatrader.ib-cost-evidence.offline.v1"
    require(verify_inputs_dummy and cost.get("trade_authority") is False
            and cost.get("actual_slippage_verified") is False
            and cost.get("fx_fee_missing_or_unproved_executions")==0
            and cost.get("fx_fee_complete_unique_executions",0)>=50
            and cost.get("orders_sent")==0
            and cost.get("period_source_sha256")==period_sha,
            "COST_RECEIPT_NOT_SAME_VERIFIED_PAPER_PERIOD")
    return config,period_sha,cost,raw_sha(cost_path)

def cost_hurdle(bid,ask,cost):
    bid,ask=float(bid),float(ask)
    require(math.isfinite(bid) and math.isfinite(ask) and 0<bid<=ask,
            "ORIGINAL_SPREAD_INVALID")
    live=(ask-bid)/((bid+ask)/2)*10000
    hist=float(cost["quote_spread_full_bps"]["p99"])
    fee=float(cost["fx_commission_one_way_bps"]["p90"])
    require(math.isfinite(hist) and math.isfinite(fee) and hist>0 and fee>0,
            "HISTORICAL_COST_RECEIPT_INVALID")
    # One full spread, both commissions, hypothetical 2bps/side slippage,
    # 2bps safety reserve. NOT a broker fill or ex-ante quote guarantee.
    return {"live_original_spread_bps":live,
            "stressed_spread_bps":max(live,hist),
            "roundtrip_commission_p90_bps":2*fee,
            "hypothetical_slippage_roundtrip_bps":4.,
            "minimum_margin_bps":2.,
            "required_absolute_prediction_bps":max(live,hist)+2*fee+6.}

def train_frozen(*, bars_path, coverage_path, merge_path, cost_path,
                 config_path, root, registry_root):
    config,period_sha,cost,cost_sha=scope(config_path,cost_path)
    root=Path(root);registry_root=Path(registry_root)
    require(root.is_absolute() and registry_root.is_absolute()
            and not root.exists(), "OUTPUT_ALREADY_USED")
    bars=read(Path(bars_path),64*1024*1024)
    coverage=read(Path(coverage_path),1024*1024)
    proof=read(Path(merge_path),1024*1024)
    require(proof.get("full_requested_coverage_proven") is True
            and proof.get("combined_count")==coverage.get("bar_count")==len(bars["bars"])
            and bars.get("research_only_revised_not_contemporaneous") is True
            and raw_sha(bars_path)==proof["output_checksums"]["paired_bars_sha256"]
            and raw_sha(coverage_path)==proof["output_checksums"]["coverage_sha256"],
            "HISTORICAL_PIN_OR_COVERAGE_FAILED")
    verify_inputs(bars,coverage,cost)
    dataset_end=max(row["time"] for row in bars["bars"])+300
    clock=time.time_ns()//1000000
    require(dataset_end*1000 < clock-86400000,
            "HISTORICAL_LABELS_NOT_SEPARATE_FROM_PROSPECTIVE")
    full=features(bars["bars"])
    X,y=full["x"],full["returns"]
    # Original ridge50 model from previously published four-fold experiment.
    # ALL already-inspected historical rows may fit a model for a genuinely
    # FUTURE study, never relabel the old test as unseen OOS.
    mdl=regression(X,y)
    means,sd,weights=(v.tolist() for v in mdl)
    require(len(means)==len(sd)==8 and len(weights)==9
            and bool(np.isfinite(np.asarray(weights)).all()),"RIDGE_NUMERIC_INVALID")
    model={"schema":"heptatrader.fx-ridge-historical-model.v1",
           "profile":HIST_REF,"historical_bars_sha256":raw_sha(bars_path),
           "historical_coverage_sha256":raw_sha(coverage_path),
           "historical_merge_sha256":raw_sha(merge_path),
           "cost_receipt_sha256":cost_sha,"period_sha256":period_sha,
           "training_code_sha256":raw_sha(Path(__file__).parent.parent/"historical_training"/"eurusd_walkforward_cost.py"),
           "research_code_sha256":CODE_SHA(),
           "trained_rows":int(len(y)),"last_matured_historical_target_ms":int(full["mature"].max()*1000),
           "source_max_bar_end_ms":dataset_end*1000,
           "mean":means,"stddev":sd,"weights":weights,
           "hyperparameter_ridge":50,"historical_holdout_previously_seen":True,
           "candidate_has_positive_validation_edge":False,
           "historical_validation_selected_action":"NO_TRADE",
           "source_prices_revised_not_original":True,
           "live_original_quote_bar_close_is_approximation":True,
           "trade_authority":False,"promotion_allowed":False,
           "broker_requests_sent":0,"broker_orders_sent":0}
    now=time.time_ns()//1000000
    # At least two minutes precommitted; one fixed 5-minute UTC grid.
    anchor=((now+120000+299999)//300000)*300000
    behavior="FX_RIDGE_PRICE_ONLY_"+digest(model)[:24]
    spec=make_spec(period_sha,behavior,horizon_ms=HORIZON_MS,
                   lookback=LOOKBACK,broker_reported_expiry=True)
    plan=register_plan(spec=spec,registered_at_ms=now,
                       first_decision_ms=anchor+1000,
                       last_decision_ms=anchor+MAX_DECISION_LATENCY_MS,
                       minimum_mature_rows=16)
    root.mkdir(mode=0o700,parents=True)
    manifest={"schema":SCHEMA,"policy_id":behavior,
              "historical_model_sha256":digest(model),
              "code_sha256":CODE_SHA(),"cost_receipt_sha256":cost_sha,
              "original_period_sha256":period_sha,
              "anchor_ms":anchor,"outcome_due_ms":anchor+HORIZON_MS,
              "source_root":str(config["feed_directory"]),
              "source_kind":"LOCAL_SHA_CHAINED_ORIGINAL_IB_PAPER",
              "mode":"PRICE_ONLY_FROZEN_RIDGE_WITH_FROZEN_ABSTAIN",
              "validation_trading_edge_qualified":False,
              "trading_behavior_propensity_known":False,
              "external_production_root_enrolled":False,
              "broker_signed_quote":False,
              "trade_authority":False,"model_promotion_allowed":False,
              "broker_requests_sent":0,"broker_orders_sent":0}
    # Only the following files are accepted; an orphaned registration is HOLD.
    write_once(root/"model.json",model)
    write_once(root/"manifest.json",manifest)
    write_once(root/"feature-spec.json",spec)
    write_once(root/"plan.json",plan)
    receipt=register_before_window(registry_root,plan,spec,Path(config["feed_directory"]))
    write_once(root/"local-registration.json",receipt)
    return {"state":"FROZEN_PRECOMMITTED_PRICE_ONLY_MODEL_NO_TRADE",
            "training_rows":model["trained_rows"],
            "plan_id":plan["plan_id"],"model_sha256":digest(model),
            "anchor_utc":datetime.fromtimestamp(anchor/1000,timezone.utc).isoformat(),
            "first_decision_ms":plan["first_decision_ms"],
            "last_decision_ms":plan["last_decision_ms"],
            "trade_authority":False,"broker_orders_sent":0}

def recover_frozen(root,config_path,cost_path):
    config,period_sha,cost,cost_sha=scope(config_path,cost_path)
    root=Path(root)
    model=read(root/"model.json")
    manifest=read(root/"manifest.json")
    plan=read(root/"plan.json")
    spec=read(root/"feature-spec.json")
    registration=read(root/"local-registration.json")
    require(manifest.get("schema")==SCHEMA
            and model.get("schema")=="heptatrader.fx-ridge-historical-model.v1"
            and manifest["historical_model_sha256"]==digest(model)
            and model["research_code_sha256"]==manifest["code_sha256"]==CODE_SHA()
            and cost_sha==manifest["cost_receipt_sha256"]==model["cost_receipt_sha256"]
            and period_sha==manifest["original_period_sha256"]==model["period_sha256"]
            and manifest["source_root"]==str(config["feed_directory"])
            and model["candidate_has_positive_validation_edge"] is False
            and model["historical_validation_selected_action"]=="NO_TRADE"
            and manifest["mode"]=="PRICE_ONLY_FROZEN_RIDGE_WITH_FROZEN_ABSTAIN"
            and plan["plan_id"]==registration["plan_id"],
            "MODEL_OR_SOURCE_OR_PLAN_CHANGED")
    verify_plan(plan)
    require(plan["feature_spec_id"]==feature_spec_id(spec)
            and spec==make_spec(period_sha,manifest["policy_id"],
                                horizon_ms=HORIZON_MS,lookback=LOOKBACK,
                                broker_reported_expiry=True)
            and plan["behavior_policy_id"]==manifest["policy_id"]
            and model["training_code_sha256"]==raw_sha(
                Path(__file__).parent.parent/"historical_training"/"eurusd_walkforward_cost.py"),
            "PLAN_FEATURE_OR_MODEL_SOURCE_CHANGED")
    return config,period_sha,cost,model,manifest,plan,spec,registration

def newest_source_chain(config,spec,at_ms):
    directory=Path(config["feed_directory"])
    paths=sorted(directory.glob("segment-*/quotes.jsonl"),key=lambda p:p.stat().st_mtime_ns)[-SOURCE_MAX_SEGMENTS:]
    require(len(paths)>=3,"NOT_ENOUGH_ORIGINAL_SEGMENTS")
    evidence=[];unique={}
    for file in paths:
        require(file.is_absolute() and not file.is_symlink()
                and not file.parent.is_symlink()
                and file.parent.parent==directory,"UNEXPECTED_FEED_SEGMENT_PATH")
        normalized,provenance=sealed_original_broker_expiry(file,spec)
        require(provenance["original_period_verified"] is True
                and provenance["historical_revisions_used"] is False,
                "NONORIGINAL_SEGMENT")
        evidence.append({"path":str(file),"complete_prefix_bytes":provenance["original_complete_bytes"],
                         "prefix_sha256":provenance["prefix_sha256"],
                         "chain_head":provenance["chain_head"]})
        for q in normalized:
            if q["available_at_ms"]>at_ms:continue
            t=q["event_at_ms"]
            prior=unique.get(t)
            if prior:
                require(prior["bid"]==q["bid"] and prior["ask"]==q["ask"],
                        "ORIGINAL_SAME_TIMESTAMP_DIFFERENT_PRICE")
            else:unique[t]=q
    return list(sorted(unique.values(),key=lambda r:(r["event_at_ms"],r["available_at_ms"]))),evidence

def bar_close_features(rows,anchor_ms):
    """Use 13 actual original quote pseudo-closes strictly known before grid.

    This is a frozen *domain-shifted* adaptation of historical 5-minute OHLC
    closes, not original IB historical 5-minute OHLC observations.
    """
    samples=[]
    for k in range(LOOKBACK):
        end=anchor_ms-(LOOKBACK-1-k)*300000
        eligible=[q for q in rows if end-QUOTE_CLOSE_MAX_AGE_MS<=q["event_at_ms"]<end
                  and q["available_at_ms"]<=end]
        require(bool(eligible),"MISSING_CAUSAL_ORIGINAL_5MIN_CLOSE")
        samples.append(max(eligible,key=lambda q:(q["event_at_ms"],q["available_at_ms"])))
    require(len({r["source_id"] for r in samples})==LOOKBACK,
            "DUPLICATED_INPUT_QUOTE_ACROSS_BUCKETS")
    close=np.asarray([(float(q["bid"])+float(q["ask"]))/2 for q in samples])
    require(bool(np.isfinite(close).all()) and bool((close>0).all()),"NONFINITE_PSEUDO_CLOSE")
    rets=np.diff(close)/close[:-1]
    hour=((anchor_ms//1000-300)//3600)%24
    feat=np.asarray([*( (close[-1]/close[-1-n]-1)*10000 for n in (1,3,6,12)),
                     float(rets.std()*10000),float(np.abs(rets).mean()*10000),
                     math.sin(2*math.pi*hour/24),math.cos(2*math.pi*hour/24)])
    return samples,feat

def decide(*, config_path,cost_path,registry_root,root):
    config,period_sha,cost,model,manifest,plan,spec,receipt=recover_frozen(root,config_path,cost_path)
    anchor=manifest["anchor_ms"]
    now=time.time_ns()//1000000
    require(plan["first_decision_ms"]<=now<=plan["last_decision_ms"],
            "OUTSIDE_REGISTERED_DECISION_WINDOW")
    rows,source_proofs=newest_source_chain(config,spec,now)
    samples,x=bar_close_features(rows,anchor)
    require(samples[-1]["available_at_ms"]<=now<samples[-1]["valid_until_ms"],
            "LAST_ORIGINAL_BROKER_QUOTE_EXPIRED")
    require(0<=now-samples[-1]["available_at_ms"]<=MAX_DECISION_LATENCY_MS,
            "LAST_ORIGINAL_QUOTE_TOO_OLD")
    from .registry import verify_registration
    witness_file=Path(source_proofs[-1]["path"])
    # The registered source root is checked, not the caller's JSON flags.
    registration=verify_registration(registry_root,plan,spec,witness_file)
    require(registration==receipt,"PRECOMMIT_RECEIPT_CHANGED")
    weights=[np.asarray(model[k],dtype=float) for k in ("mean","stddev","weights")]
    prediction=float(predict(tuple(weights),x.reshape(1,-1))[0])
    require(math.isfinite(prediction),"NONFINITE_FROZEN_FORECAST")
    latest=samples[-1]
    hurdle=cost_hurdle(latest["bid"],latest["ask"],cost)
    forecast_direction="UP" if prediction>0 else "DOWN" if prediction<0 else "FLAT"
    # Even a large forecast cannot turn on trading; old 4-fold validation
    # selected abstention. A signed original trader propensity is unavailable.
    frozen={"schema":"heptatrader.fx-ridge-original-preoutcome.v1",
            "model_sha256":digest(model),"model_file_sha256":raw_sha(Path(root)/"model.json"),
            "manifest_sha256":raw_sha(Path(root)/"manifest.json"),
            "plan_id":plan["plan_id"],"registered_at_ms":receipt["sealed_at_ms"],
            "anchor_ms":anchor,"decision_at_ms":now,
            "outcome_mature_not_before_ms":anchor+HORIZON_MS,
            "history_quote_samples":samples,"source_prefix_receipts":source_proofs,
            "feature_vector":x.tolist(),"forecast_midpoint_bps":prediction,
            "forecast_direction_research_only":forecast_direction,
            "cost_hurdle":hurdle,
            "forecast_magnitude_clears_cost":abs(prediction)>hurdle["required_absolute_prediction_bps"],
            "selected_trading_action":"NO_TRADE",
            "original_trader_behavior_propensity_attested":False,
            "native_owner_current":False,"local_chain_not_broker_signed":True,
            "actual_execution_reward":False,"broker_requests_sent":0,
            "broker_orders_sent":0,"trade_authority":False,"promotion_allowed":False}
    write_once(Path(root)/"preoutcome-decision.json",frozen)
    sealed_at=time.time_ns()//1000000
    q={"schema":"heptatrader.fx-ridge-preoutcome-qualification.v1",
       "decision_sha256":raw_sha(Path(root)/"preoutcome-decision.json"),
       "seal_at_ms":sealed_at,
       "last_original_quote_expiry_ms":latest["valid_until_ms"],
       "sealed_before_broker_reported_quote_expiry":sealed_at<latest["valid_until_ms"],
       "precommitted_registration_proven_locally":True,
       "selected_trading_action":"NO_TRADE",
       "state":("PRICE_ONLY_PREDICTION_FROZEN_NO_TRADE" if sealed_at<latest["valid_until_ms"]
                else "HOLD_DECISION_DURABLE_AFTER_QUOTE_EXPIRY"),
       "trade_authority":False,"model_promotion_allowed":False,
       "broker_requests_sent":0,"broker_orders_sent":0}
    write_once(Path(root)/"qualification.json",q)
    return q

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode",required=True,choices=("preseal","decide"))
    for arg in ("config","cost","root","registry-root"):
        p.add_argument("--"+arg,required=True,type=Path)
    for arg in ("bars","coverage","merge-proof"):
        p.add_argument("--"+arg,type=Path)
    a=p.parse_args()
    os.umask(0o077)
    shared={"config_path":a.config,"cost_path":a.cost,
            "root":a.root,"registry_root":a.registry_root}
    if a.mode=="preseal":
        require(all((a.bars,a.coverage,a.merge_proof)),"FROZEN_TRAIN_DATA_REQUIRED")
        r=train_frozen(bars_path=a.bars,coverage_path=a.coverage,
                       merge_path=a.merge_proof,**shared)
    else:r=decide(**shared)
    print(json.dumps(r,sort_keys=True),flush=True)
    if str(r.get("state","")).startswith("HOLD"):raise SystemExit(2)
if __name__=="__main__":main()
