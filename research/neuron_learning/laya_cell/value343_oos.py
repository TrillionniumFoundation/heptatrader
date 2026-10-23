"""Pocket4 exact-original off-policy EUR/USD net-value chronological CPU research.

This research reads the existing immutable original REQUEST/COMMAND_BINDING
prefix receipts, authoritative execution+commission evidence and the Laya
settled-reward snapshot. No broker requests, new quote reads, GPU service,
live predictor, model selection, trade action or producer state is modified.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import time
import numpy as np

from .integrity import ContractError,digest
from hepta_decision_attribution import read_private,strict,verified_bindings

REWARDS=Path("/home/alex/.local/state/heptatrader/neuron-learning/laya-resident-reset-1791349390533/control/rewards-status.json")
CONFIG=Path("/home/alex/.local/state/heptatrader/operator-evidence/laya-fx-active-manifest-registration-20261016-r1/trainer-fx-reward-config.v1.json")
OMS=Path("/home/alex/.local/state/heptatrader/execution/oms-journal.jsonl")
BEHAVIOR="6957e3fc0a86443581c4fcef86b08d655f128ccfb298d433e57abc6d5f39934c"
FEATURES=("sell","signed_trend_3_bps","signed_trend_8_bps","signed_trend_all_bps",
          "sample_return_vol_bps","past_range_bps","latest_spread_bps",
          "mean_spread_bps","mean_step_bps_signed","positive_step_share_signed",
          "quote_age_sec","history_span_min","hour_sin","hour_cos",
          "weekday_sin","weekday_cos","last_vs_mean_bps_signed")
SCHEMA="heptatrader.pocket4-original-value343-fixed-historical-oos.v1"
TRAIN_FRACTION=.65
TEST_START_FRACTION=.80
HUBER_DELTA_USD=25.0
RIDGE_LAMBDA=20.0
IRLS_STEPS=20


def finite(x):
    if type(x) not in (int,float) or not math.isfinite(x):
        raise ContractError("VALUE343_NONFINITE_NUMERIC")
    return float(x)


def read_snapshot():
    blob=read_private(REWARDS,limit=32*1024**2)
    report=strict(blob)
    rows=report.get("rewards")
    if (report.get("broker_orders_sent")!=0 or
            not isinstance(rows,list) or len(rows)!=343):
        raise ContractError("VALUE343_REWARD_SOURCE_COUNT_OR_AUTHORITY")
    main=[r for r in rows if r["input_attribution"]["producer_manifest_sha256"]==BEHAVIOR]
    if len(main)!=335:
        raise ContractError("VALUE343_FIXED_ORIGINAL_BEHAVIOR_SCOPE_CHANGED")
    if len({r["reward_id"] for r in rows})!=len(rows):
        raise ContractError("VALUE343_REWARD_ID_COLLISION")
    return blob,report,main


def original_requests(main, period_sha256):
    config=strict(read_private(CONFIG,limit=2*1024**2))
    if config["period_sha256"]!=period_sha256:
        raise ContractError("VALUE343_PERIOD_CHANGED")
    scopes=[r for r in config["decision_attribution_registrations"]["EUR.USD"]
            if r.get("sha256")==BEHAVIOR]
    if len(scopes)!=1:
        raise ContractError("VALUE343_PINNED_BEHAVIOR_REGISTRATION_NOT_UNIQUE")
    original,issues=verified_bindings(scopes[0],"EUR.USD",period_sha256)
    if issues:
        # One frozen original behavior population with zero invalid producer
        # graphs. Never silently ignore a broken attribution source.
        raise ContractError("VALUE343_ORIGINAL_PRODUCER_ATTRIBUTION_ISSUES")
    source={}
    for reward in main:
        witness=reward["input_attribution"]
        if witness["request_id"] in source:
            raise ContractError("VALUE343_DUPLICATE_REQUEST")
        command=reward["open_command_id"]
        binding=original.get(command)
        if not isinstance(binding,dict):
            raise ContractError("VALUE343_ORIGINAL_BINDING_MISSING")
        if (binding.get("input_id")!=reward["input_id"]
                or binding.get("forecast_id")!=reward["forecast_id"]
                or binding.get("model_id")!=reward["behavior_model_id"]
                or binding.get("instrument")!="EUR.USD"
                or binding.get("side")!=reward["executions"][0]["side"]
                or binding.get("producer_manifest_sha256")!=BEHAVIOR
                or binding.get("request_id")!=witness["request_id"]
                or binding.get("prompt_sha256")!=witness["prompt_sha256"]):
            raise ContractError("VALUE343_ORIGINAL_BINDING_CHANGED")
        source[witness["request_id"]]=reward
    prefixes=defaultdict(dict)
    for reward in main:
        a=reward["input_attribution"]
        if len(a["source_prefixes"])!=1:
            raise ContractError("VALUE343_MULTIPART_PREFIX_UNREVIEWED")
        w=a["source_prefixes"][0]
        p=w["path"];n=w["bytes"];sha=w["sha256"]
        if p in prefixes and n in prefixes[p] and prefixes[p][n]!=sha:
            raise ContractError("VALUE343_SOURCE_PREFIX_CONFLICT")
        prefixes[p][n]=sha
    requests={};commands={}
    for file,lengths in sorted(prefixes.items()):
        size=max(lengths)
        if size>20*1024**2:
            raise ContractError("VALUE343_ORIGINAL_JOURNAL_CAP")
        blob=read_private(file,limit=20*1024**2,prefix_bytes=size)
        for length,sha in lengths.items():
            if hashlib.sha256(blob[:length]).hexdigest()!=sha:
                raise ContractError("VALUE343_ORIGINAL_PREFIX_HASH_CHANGED")
        for line in blob.splitlines():
            z=strict(line);record=z.get("data") or {}
            rid=record.get("request_id")
            if rid not in source:continue
            if z.get("kind")=="REQUEST":
                if rid in requests and requests[rid]!=record:
                    raise ContractError("VALUE343_REQUEST_REWRITTEN")
                requests[rid]=(record,z["sequence"],z.get("captured_at_ms"))
            elif z.get("kind")=="COMMAND_BINDING":
                if rid in commands and commands[rid]!=record:
                    raise ContractError("VALUE343_COMMAND_REWRITTEN")
                commands[rid]=(record,z["sequence"],z.get("captured_at_ms"))
    if len(requests)!=len(main) or len(commands)!=len(main):
        raise ContractError("VALUE343_MISSING_ORIGINAL_REQUEST_COMMAND")
    return requests,commands,len(prefixes),len(original)


def make_features(request,side):
    if (request.get("instrument")!="EUR.USD" or
            type(request.get("started_at_ms")) is not int or
            request.get("position_at_request")!=0 or
            side not in ("BUY","SELL")):
        raise ContractError("VALUE343_PRETRADE_SUBJECT")
    history=request.get("history_snapshot")
    if not isinstance(history,list) or not 6<=len(history)<=24:
        raise ContractError("VALUE343_CAUSAL_QUOTE_COUNT")
    mids=[];spreads=[];timestamps=[];prev=-1
    for q in history:
        if not isinstance(q,dict) or type(q.get("observed_at_ms")) is not int:
            raise ContractError("VALUE343_QUOTE_SHAPE")
        t=q["observed_at_ms"]
        if t<=prev or t>request["started_at_ms"]:
            raise ContractError("VALUE343_FUTURE_OR_UNSORTED_QUOTE")
        prev=t
        bid,ask,mid=(finite(q[k]) for k in ("bid","ask","mid"))
        if not 0<bid<=ask or abs(mid-(bid+ask)/2)>1e-9:
            raise ContractError("VALUE343_QUOTE_BID_ASK_MID_INCONSISTENT")
        mids.append(mid);spreads.append(10000*(ask-bid)/mid)
        timestamps.append(t)
    age=(request["started_at_ms"]-timestamps[-1])/1000.0
    if not 0<=age<=30:
        raise ContractError("VALUE343_QUOTE_STALE_AT_ORIGINAL_REQUEST")
    side_sign=1.0 if side=="BUY" else -1.0
    def ret(a,b):return 10000*(mids[a]/mids[b]-1)
    steps=[ret(i,i-1) for i in range(1,len(mids))]
    day=datetime.fromtimestamp(request["started_at_ms"]/1000,timezone.utc)
    hour=(day.hour+day.minute/60+day.second/3600)
    weekday=day.weekday()
    vector=[
        1.0 if side=="SELL" else -1.0,
        side_sign*ret(-1,max(0,len(mids)-4)),
        side_sign*ret(-1,max(0,len(mids)-9)),
        side_sign*ret(-1,0),
        math.sqrt(sum((x-sum(steps)/len(steps))**2 for x in steps)/len(steps)),
        10000*(max(mids)-min(mids))/mids[-1],
        spreads[-1],sum(spreads)/len(spreads),
        side_sign*sum(steps)/len(steps),
        sum(1 if side_sign*x>0 else 0 for x in steps)/len(steps),
        age,(timestamps[-1]-timestamps[0])/60000,
        math.sin(2*math.pi*hour/24),math.cos(2*math.pi*hour/24),
        math.sin(2*math.pi*weekday/7),math.cos(2*math.pi*weekday/7),
        side_sign*10000*(mids[-1]/(sum(mids)/len(mids))-1)]
    if len(vector)!=len(FEATURES) or any(not math.isfinite(x) for x in vector):
        raise ContractError("VALUE343_FEATURE_VECTOR")
    return vector


def original_oms_events(main):
    all_ids={e["execution_id"] for reward in main for e in reward["executions"]}
    if len(all_ids)<len(main)*2:
        raise ContractError("VALUE343_EXECUTION_IDS_NOT_UNIQUE")
    blob=read_private(OMS,limit=128*1024**2)
    if not blob.endswith(b"\n"):
        raise ContractError("VALUE343_ORIGINAL_OMS_INCOMPLETE_TAIL")
    first={}
    for line in blob.splitlines():
        x=strict(line)
        event=x.get("event");eid=x.get("broker_execution_id")
        if event not in ("broker_execution","broker_commission") or eid not in all_ids:
            continue
        if x.get("execution_domain")!="PAPER" or x.get("venue")!="IB":
            continue
        if event=="broker_execution" and x.get("instrument")!="EUR.USD":
            continue
        if event=="broker_commission" and x.get("broker_econ_commission_currency")!="USD":
            continue
        ts=x.get("ts_ms")
        if type(ts) is not int or ts<=0:
            raise ContractError("VALUE343_ORIGINAL_BROKER_EVENT_TIME")
        key=(eid,event)
        if key not in first or ts<first[key]["ts_ms"]:
            first[key]=x
    if any((eid,k) not in first for eid in all_ids for k in ("broker_execution","broker_commission")):
        raise ContractError("VALUE343_MISSING_ORIGINAL_EXECUTION_OR_FEE")
    return first,{"oms_read_bytes":len(blob),"oms_sha256":hashlib.sha256(blob).hexdigest(),
                  "verified_execution_ids":len(all_ids)}


def verify_100k_reward(reward, request, binding):
    """Current 100K USD reference notional; NEVER the retired 25K EUR profile.

    Recompute exact native economics and verify the original pre-open notional
    command. Variable EUR order quantity is intentional under USD notional.
    """
    command=binding["command"]
    if (command.get("quantity_mode")!="BASE_NOTIONAL_TARGET"
            or Decimal(str(command.get("target_notional_usd")))!=Decimal(100000)
            or Decimal(str(command.get("position_before_send")))!=0
            or Decimal(str(command.get("order_quantity")))<=0
            or command.get("side") not in ("BUY","SELL")
            or command.get("instrument")!="EUR.USD"
            or Decimal(str(command.get("reference_price")))<=0):
        raise ContractError("VALUE343_ORIGINAL_USD100K_COMMAND_MISMATCH")
    flags=("learning_eligible","exact_input_bound","settlement_complete",
           "fees_complete","flat_to_flat_executed_quantity")
    if (reward.get("reward_id")!=digest({k:v for k,v in reward.items() if k!="reward_id"})
            or any(reward.get(k) is not True for k in flags)
            or reward.get("policy_gradient_allowed") is not False
            or reward.get("laya_on_policy") is not False
            or reward.get("effect_authority") is not False
            or reward.get("learning_exclusion_reasons")
            or reward.get("unsettled_commands")
            or request.get("instrument")!="EUR.USD"
            or reward.get("instrument")!="EUR.USD"
            or reward.get("currency")!="USD"
            or request.get("model_id")!=reward["behavior_model_id"]
            or digest(request)!=reward["input_id"]
            or hashlib.sha256(request["prompt"].encode()).hexdigest()!=reward["input_attribution"]["prompt_sha256"]):
        raise ContractError("VALUE343_ORIGINAL_REWARD_OR_REQUEST_INVALID")
    position=cash=fee=opened=Decimal(0)
    ids=set()
    for execution in reward["executions"]:
        eid=execution["execution_id"]
        if eid in ids or execution.get("commission_currency")!="USD":
            raise ContractError("VALUE343_DUPLICATE_EXECUTION_OR_UNSETTLED_FEE")
        ids.add(eid)
        q=Decimal(execution["quantity"]);p=Decimal(execution["price"])
        f=Decimal(execution["commission"])
        if (not all(v.is_finite() for v in (q,p,f)) or q<=0 or p<=0 or f<0
                or execution["side"] not in ("BUY","SELL")):
            raise ContractError("VALUE343_INVALID_EXECUTION_PRICE_OR_FEE")
        signed=q if execution["side"]=="BUY" else -q
        if position and position*(position+signed)<0:
            raise ContractError("VALUE343_EXECUTION_CROSSES_FLAT")
        position+=signed;cash-=signed*p;fee+=f
        if execution["command_id"]==reward["open_command_id"]:
            if execution["side"]!=command["side"]:
                raise ContractError("VALUE343_OPEN_EXECUTION_DIRECTION_CHANGED")
            opened+=q
    if (position!=0 or opened!=Decimal(str(command["order_quantity"]))
            or cash!=Decimal(reward["realized_gross_usd"])
            or fee!=Decimal(reward["actual_commissions_usd"])
            or cash-fee!=Decimal(reward["realized_net_usd"])):
        raise ContractError("VALUE343_USD100K_ECONOMIC_RECOMPUTATION")
    # The original command's USD-budget / reference-price conversion may
    # round to integral EUR, so check tolerance rather than assert 25,000.
    reference_notional=opened*Decimal(str(command["reference_price"]))
    if abs(reference_notional-Decimal(100000))>Decimal(2):
        raise ContractError("VALUE343_USD100K_QUANTITY_REFERENCE_OUTSIDE_TOLERANCE")
    return {"net_usd":str(cash-fee),"gross_usd":str(cash),
            "fee_usd":str(fee),"row_id":digest({"reward_id":reward["reward_id"],
                "request_id":reward["input_id"],"frozen_command":digest(command)})}


def settled_time(reward,first,request,binding):
    current=Decimal(0);fee=Decimal(0);seen=set();times=[]
    for execution in reward["executions"]:
        eid=execution["execution_id"]
        if eid in seen:
            raise ContractError("VALUE343_DUPLICATE_EXECUTION")
        seen.add(eid)
        ex=first[eid,"broker_execution"];co=first[eid,"broker_commission"]
        px=Decimal(str(ex.get("broker_econ_execution_price")))
        commiss=Decimal(str(co.get("broker_econ_commission")))
        expected_px=Decimal(execution["price"])
        expected_fee=Decimal(execution["commission"])
        if (abs(px-expected_px)>Decimal(".0000001")
                or abs(commiss-expected_fee)>Decimal(".00001")):
            raise ContractError("VALUE343_FIRST_BROKER_ECON_CHANGED")
        current+=px;fee+=commiss
        times.extend([ex["ts_ms"],co["ts_ms"]])
    known=max(times)
    if (known<binding["command"]["created_at_ms"]
            or min(times)<request["started_at_ms"]
            or known>=int(time.time()*1000)+10000):
        raise ContractError("VALUE343_OUTCOME_TIME_RELATION")
    return known


def build_rows(snapshot,main,period):
    requests,commands,nfiles,nbindings=original_requests(main,period)
    events,oms_witness=original_oms_events(main)
    rows=[];seen=set()
    for reward in main:
        rid=reward["input_attribution"]["request_id"]
        request,rseq,_=requests[rid]
        binding,bseq,_=commands[rid]
        if (rseq>=bseq or binding["request_id"]!=rid
                or binding["command"]["kind"]!="OPEN"
                or binding["command"]["command_id"]!=reward["open_command_id"]
                or binding["state_write_succeeded"] is not True
                or binding["basis"]!="IDENTICAL_RETURNED_OBJECT_AT_ORIGINAL_DURABLE_PREPARED"
                or binding["request_sha256"]!=digest(request)
                or binding["decision_sha256"]!=reward["forecast_id"]
                or not request["started_at_ms"]<=binding["decision_available_at_ms"]<=binding["command"]["created_at_ms"]):
            raise ContractError("VALUE343_PRETRADE_COMMAND_CAUSAL_CHAIN")
        side=binding["command"]["side"]
        if side!=reward["executions"][0]["side"]:
            raise ContractError("VALUE343_ACTUAL_ORDER_DIRECTION_CHANGED")
        # The established audited monetary computation verifies original
        # realized gross, every commission, flat-to-flat quantity and bound
        # request; do not build labels from a synthetic spread/fee model.
        verified=verify_100k_reward(reward,request,binding)
        if reward["reward_id"] in seen:
            raise ContractError("VALUE343_DUPLICATE_TRAINING_TARGET")
        seen.add(reward["reward_id"])
        known=settled_time(reward,events,request,binding)
        rows.append({"row_id":verified["row_id"],
                     "reward_id":reward["reward_id"],
                     "input_started_at_ms":request["started_at_ms"],
                     "outcome_first_fully_known_ms":known,
                     "features":make_features(request,side),
                     "net_usd":float(verified["net_usd"]),
                     "gross_usd":float(verified["gross_usd"]),
                     "actual_fee_usd":float(verified["fee_usd"]),
                     "side":side})
    rows.sort(key=lambda r:(r["input_started_at_ms"],r["row_id"]))
    if len(rows)!=335 or len({r["row_id"] for r in rows})!=335:
        raise ContractError("VALUE343_COHORT_SIZE_OR_ROW_COLLISION")
    return rows,{"original_attribution_files":nfiles,
                 "verified_original_bindings":nbindings,**oms_witness}


def fixed_split(rows):
    """Fixed request-time cutoffs only, no price/PnL-selected boundary."""
    if not isinstance(rows,list) or len(rows)!=335:
        raise ContractError("VALUE343_FIXED_POPULATION_REQUIRED")
    order=[(r["input_started_at_ms"],r["row_id"]) for r in rows]
    if order!=sorted(order) or len({r["reward_id"] for r in rows})!=len(rows):
        raise ContractError("VALUE343_CHRONOLOGICAL_IDENTITY")
    train_end=int(len(rows)*TRAIN_FRACTION)
    test_start=int(len(rows)*TEST_START_FRACTION)
    train,embargo,hold=rows[:train_end],rows[train_end:test_start],rows[test_start:]
    test_time=hold[0]["input_started_at_ms"]
    # This is mandatory. A trade opened early but with a close/commission
    # available after the test start was NOT a known training label.
    if any(r["outcome_first_fully_known_ms"]>test_time for r in train):
        raise ContractError("VALUE343_TRAINING_REWARD_NOT_YET_KNOWN")
    if any(r["input_started_at_ms"]<test_time for r in hold):
        raise ContractError("VALUE343_TEST_WINDOW_OVERLAP")
    if len(train)<150 or len(hold)<50 or len(embargo)<20:
        raise ContractError("VALUE343_EMBARGO_OR_TRAIN_COVERAGE")
    if any(r["input_started_at_ms"]>r["outcome_first_fully_known_ms"] for r in rows):
        raise ContractError("VALUE343_OUTCOME_KNOWN_BEFORE_INPUT")
    return train,embargo,hold


def design_from_train_only(train, test):
    """Fit normalizer ONLY on the training window; never future outcomes."""
    x=np.asarray([r["features"] for r in train],dtype=np.float64)
    z=np.asarray([r["features"] for r in test],dtype=np.float64)
    if x.shape!=(len(train),len(FEATURES)) or z.shape!=(len(test),len(FEATURES)):
        raise ContractError("VALUE343_FEATURE_DIMENSIONS")
    if not np.isfinite(x).all() or not np.isfinite(z).all():
        raise ContractError("VALUE343_NONFINITE_FEATURES")
    mean=x.mean(axis=0)
    raw_scale=x.std(axis=0)
    scale=np.maximum(raw_scale,1e-5)
    x=np.clip((x-mean)/scale,-5,5)
    z=np.clip((z-mean)/scale,-5,5)
    train_design=np.concatenate((np.ones((len(x),1)),x),axis=1)
    test_design=np.concatenate((np.ones((len(z),1)),z),axis=1)
    return train_design,test_design,{"means":mean.tolist(),
                                    "scales":scale.tolist(),
                                    "normalizer_train_rows":len(train),
                                    "clip_after_train_scaling":5.0}


def fit_huber(train_design, net_targets):
    """20 real, deterministic robust regularized weighted least-squares updates."""
    if train_design.ndim!=2 or train_design.shape[1]!=len(FEATURES)+1:
        raise ContractError("VALUE343_FIXED_MODEL_DESIGN")
    y=np.asarray(net_targets,dtype=np.float64)/100.0
    if y.shape!=(len(train_design),) or not np.isfinite(y).all() or np.any(np.abs(y)>8):
        raise ContractError("VALUE343_NET_TARGETS")
    w=np.zeros(train_design.shape[1],dtype=np.float64)
    w[0]=float(np.median(y))
    penalty=np.diag([0.0]+[RIDGE_LAMBDA]*(len(w)-1))
    trajectory=[]
    delta=HUBER_DELTA_USD/100.0
    for step in range(IRLS_STEPS):
        err=y-train_design@w
        weights=np.minimum(1.0,delta/np.maximum(np.abs(err),1e-12))
        x_weighted=train_design*weights[:,None]
        candidate=np.linalg.solve(
            train_design.T@x_weighted+penalty,
            train_design.T@(weights*y))
        if not np.isfinite(candidate).all():
            raise ContractError("VALUE343_NONFINITE_TRAINING_STEP")
        w=candidate
        residual=(train_design@w-y)*100
        abs_res=np.abs(residual)
        huber=np.where(abs_res<=HUBER_DELTA_USD,
                       0.5*residual**2/HUBER_DELTA_USD,
                       abs_res-HUBER_DELTA_USD/2)
        trajectory.append(round(float(huber.mean()),12))
    if len(trajectory)!=IRLS_STEPS or any(not math.isfinite(z) for z in trajectory):
        raise ContractError("VALUE343_UNVERIFIED_OPTIMIZER")
    return w,{"actual_optimizer_steps":len(trajectory),
              "loss_by_step":trajectory,
              "coefficients":w.tolist(),
              "lambda":RIDGE_LAMBDA,
              "huber_delta_usd":HUBER_DELTA_USD,
              "target_divisor_usd":100.0,
              "features":list(FEATURES),
              "nonzero_feature_coefficients":int(np.count_nonzero(w[1:]))}


def predict_net(design, weights):
    out=np.asarray(design@weights*100.0,dtype=np.float64)
    if not np.isfinite(out).all():
        raise ContractError("VALUE343_PREDICTION_NONFINITE")
    return np.clip(out,-800,800)


def evaluate(rows, predicted):
    y=np.asarray([r["net_usd"] for r in rows],dtype=np.float64)
    p=np.asarray(predicted,dtype=np.float64)
    if y.shape!=p.shape or not len(y) or not np.isfinite(y).all() or not np.isfinite(p).all():
        raise ContractError("VALUE343_SCORE_DENOMINATOR")
    errors=p-y
    absolute=np.abs(errors)
    huber=np.where(absolute<=HUBER_DELTA_USD,0.5*errors**2/HUBER_DELTA_USD,
                   absolute-HUBER_DELTA_USD/2)
    return {"rows":len(rows),"mae_usd":float(absolute.mean()),
            "rmse_usd":float(np.sqrt(np.mean(errors**2))),
            "huber_usd":float(huber.mean()),
            "prediction_mean_usd":float(p.mean()),
            "target_mean_usd":float(y.mean()),
            "actual_positive_net_count":int(np.count_nonzero(y>0)),
            "predicted_positive_net_count":int(np.count_nonzero(p>0))}


def results(rows,source_sha,oms_witness):
    train,embargo,hold=fixed_split(rows)
    design_train,design_hold,normalizer=design_from_train_only(train,hold)
    train_targets=[r["net_usd"] for r in train]
    coefficients,fit=fit_huber(design_train,train_targets)
    baselines={
        "zero_usd":0.0,
        "training_mean_usd":float(np.mean(train_targets)),
        "training_median_usd":float(np.median(train_targets)),
        "negative_training_mean_fee_usd":-float(np.mean([r["actual_fee_usd"] for r in train]))
    }
    holdout_metrics={
        **{name:evaluate(hold,[value]*len(hold)) for name,value in baselines.items()},
        "huber_robust_value17":evaluate(hold,predict_net(design_hold,coefficients))
    }
    train_metrics={
        "training_median":evaluate(train,[baselines["training_median_usd"]]*len(train)),
        "huber_robust_value17":evaluate(train,predict_net(design_train,coefficients))}
    holdout_days=Counter(datetime.fromtimestamp(
        r["input_started_at_ms"]/1000,timezone.utc).strftime("%Y-%m-%d") for r in hold)
    all_y=[r["net_usd"] for r in rows]
    for r in rows:
        if abs((r["gross_usd"]-r["actual_fee_usd"])-r["net_usd"])>1e-4:
            raise ContractError("VALUE343_NET_AFTER_ACTUAL_FEES")
    model_better_than_median=(holdout_metrics["huber_robust_value17"]["mae_usd"]+
                             1e-8<holdout_metrics["training_median_usd"]["mae_usd"])
    report={"schema":SCHEMA,"source_reward_status_sha256":source_sha,
            "producer_manifest_sha256":BEHAVIOR,
            "snapshot_rows_total":343,"fixed_behavior_rows":len(rows),
            "feature_scope":"ORIGINAL_REQUEST_HISTORY_SNAPSHOT_AND_PREOPEN_SIDE_ONLY",
            "feature_name_count":len(FEATURES),
            "all_reward_targets_after_actual_reported_commissions":True,
            "train_rows":len(train),"chronological_embargo_rows":len(embargo),
            "historical_holdout_rows":len(hold),
            "train_request_end_ms":train[-1]["input_started_at_ms"],
            "holdout_request_start_ms":hold[0]["input_started_at_ms"],
            "train_final_outcome_known_ms":max(r["outcome_first_fully_known_ms"] for r in train),
            "no_training_outcome_matures_after_holdout_start":True,
            "holdout_utc_days":dict(sorted(holdout_days.items())),
            "all335_net_sum_usd":float(sum(all_y)),
            "all335_actual_reported_fee_sum_usd":float(sum(r["actual_fee_usd"] for r in rows)),
            "all335_gross_sum_usd":float(sum(r["gross_usd"] for r in rows)),
            "train_sum_net_usd":float(sum(r["net_usd"] for r in train)),
            "test_sum_net_usd":float(sum(r["net_usd"] for r in hold)),
            "train_baseline_constants_usd":baselines,
            "normalizer":normalizer,"model":fit,
            "holdout_metrics":holdout_metrics,"train_metrics":train_metrics,
            "model_mae_beats_train_median":model_better_than_median,
            "economic_promotion_supported":False,
            "selective_trade_policy_evaluated":False,
            "original_behavior_off_policy_only":True,
            "no_counterfactual_order_conclusion":True,
            "no_live_model_deployed":True,
            "no_live_trading_service_changed":True,
            "laya_on_policy_episodes":0,
            "laya_action_policy_gradient_steps":0,
            "broker_orders_sent":0,"model_promoted":False,
            "oms_witness":oms_witness,
            "source_original_rows_digest":digest([r["row_id"] for r in rows]),
            "model_qualification":"HISTORICAL_TIME_SPLIT_RESEARCH_ONLY"}
    report["report_id"]=digest(report)
    return report


def write_once(output,value):
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ContractError("VALUE343_OUTPUT_PRIVATE_NEW_ONLY")
    parent=output.parent
    if parent.resolve()!=Path(
            "/home/alex/.local/state/heptatrader/operator-evidence/value343-chronological-oos-20261016").resolve():
        raise ContractError("VALUE343_OUTPUT_LOCATION")
    parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.umask(0o077)
    data=(json.dumps(value,sort_keys=True,allow_nan=False,
                     separators=(",",":"))+"\n").encode()
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
    with os.fdopen(fd,"wb") as handle:
        handle.write(data);handle.flush();os.fsync(handle.fileno())
    d=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(d)
    finally:os.close(d)
    return hashlib.sha256(data).hexdigest()


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt",type=Path,required=True)
    args=parser.parse_args()
    reward_raw,reward,main_rows=read_snapshot()
    config=strict(read_private(CONFIG,limit=2*1024**2))
    rows,witness=build_rows(reward,main_rows,config["period_sha256"])
    report=results(rows,hashlib.sha256(reward_raw).hexdigest(),witness)
    report["program_sha256"]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["receipt_id"]=digest(report)
    h=write_once(args.receipt,report)
    print(json.dumps({"schema":SCHEMA,"receipt":str(args.receipt),
         "receipt_sha256":h,"report_id":report["report_id"],
         "train_rows":report["train_rows"],
         "embargo_rows":report["chronological_embargo_rows"],
         "holdout_rows":report["historical_holdout_rows"],
         "baselines":report["holdout_metrics"],
         "research_only":True,"broker_orders_sent":0},sort_keys=True))


if __name__=="__main__":
    main()
