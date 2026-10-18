"""Fixed conditional-admission, five-session original-price observation.

Local durable receipts are not an independent timestamp authority. This
research contract grants no trade, model promotion or statistical authority.
"""
from __future__ import annotations
from bisect import bisect_right
from datetime import datetime, timedelta, time as wall_time, timezone
from math import isfinite, log
from pathlib import Path
from zoneinfo import ZoneInfo
from .integrity import ContractError, digest, read_json, sha_file
from .resident_store import write_once
from .price_year_gpu import FEATURE_NAMES
from .price_original_live_features import original_closed_bars, original_feature_snapshot

BAR_MS=300000
INPUT_DEADLINE_MS=5000
OUTCOME_GRACE_MS=60000
WARMUP_MS=13*BAR_MS
WAIT_MS=7*86400000
SESSIONS=5
NY=ZoneInfo("America/New_York")
SCHEMA="heptatrader.original-price-five-session.v1"

def stamp(value, key):
    return {**value, key:digest(value)}

def verify_stamp(value, key):
    if not isinstance(value,dict) or value.get(key)!=digest({k:v for k,v in value.items() if k!=key}):
        raise ContractError("FIVE_SESSION_IMMUTABLE_IDENTITY")
    return value

def calendar_sessions(after_ms):
    day=datetime.fromtimestamp(after_ms/1000,timezone.utc).astimezone(NY).date()
    sessions=[]
    for delta in range(16):
        date=day+timedelta(days=delta)
        if date.weekday() not in (6,0,1,2,3):continue
        opening=datetime.combine(date,wall_time(17,15),NY)
        closing=datetime.combine(date+timedelta(days=1),wall_time(17,0),NY)
        start=int(opening.timestamp()*1000);end=int(closing.timestamp()*1000)
        if start<=after_ms:continue
        sessions.append({"session":len(sessions),"open_ms":start,"close_ms":end,
            "open_new_york":opening.isoformat(),"close_new_york":closing.isoformat(),
            "warmup_end_ms":start+WARMUP_MS})
        if len(sessions)==SESSIONS:return sessions
    raise ContractError("FIVE_SESSION_CALENDAR_BOUND")

def register(now_ms,config_sha256):
    if type(now_ms) is not int or now_ms<=0 or len(config_sha256)!=64:
        raise ContractError("FIVE_SESSION_REGISTRATION")
    deadline=now_ms+WAIT_MS
    return stamp({"schema":SCHEMA,"created_at_ms":now_ms,
        "admission_deadline_ms":deadline,
        "hard_end_ms":calendar_sessions(deadline)[-1]["close_ms"]+OUTCOME_GRACE_MS,
        "config_sha256":config_sha256,"admission_check_seconds":60,
        "start_rule":"FIRST_VERIFIED_READY_THEN_NEXT_FULL_REGULAR_SESSION",
        "session_count":SESSIONS,"structural_warmup_ms_per_session":WARMUP_MS,
        "input_receipt_cutoff":"ANCHOR","outcome_receipt_cutoff":"TARGET_CLOSE",
        "input_persistence_deadline_ms":INPUT_DEADLINE_MS,
        "outcome_persistence_grace_ms":OUTCOME_GRACE_MS,
        "calendar_holidays_and_exceptional_outages_not_proven":True,
        "missing_after_commit_remain_in_denominator":True,
        "historical_training_and_original_quote_domains_differ":True,
        "trade_authority":False,"broker_orders_sent":0,"model_promotion":False,
        "formal_laya_trial":False,"economic_alpha_proven":False},"registration_id")

def make_plan(registration,ready_at_ms,ready_evidence_sha256):
    verify_stamp(registration,"registration_id")
    if not registration["created_at_ms"]<=ready_at_ms<=registration["admission_deadline_ms"]:
        raise ContractError("FIVE_SESSION_ADMISSION_EXPIRED")
    sessions=calendar_sessions(ready_at_ms)
    anchors=[]
    for s in sessions:
        for anchor in range(s["warmup_end_ms"],s["close_ms"],BAR_MS):
            anchors.append({"slot":len(anchors),"session":s["session"],"anchor_ms":anchor})
    return stamp({"schema":SCHEMA+".plan","registration_id":registration["registration_id"],
        "ready_at_ms":ready_at_ms,"ready_evidence_sha256":ready_evidence_sha256,
        "sessions":sessions,"anchors":anchors,"eligible_opportunities":len(anchors),
        "last_outcome_deadline_ms":sessions[-1]["close_ms"]+OUTCOME_GRACE_MS,
        "trade_authority":False},"plan_id")

def check_plan(plan,registration):
    verify_stamp(plan,"plan_id")
    wanted=make_plan(registration,plan["ready_at_ms"],plan["ready_evidence_sha256"])
    if plan!=wanted:raise ContractError("FIVE_SESSION_CALENDAR_CHANGED")
    return plan

def check_probs(probs):
    if (not isinstance(probs,list) or len(probs)!=7 or
        any(type(x) not in (int,float) or not isfinite(x) or not 0<=x<=1 for x in probs)
        or abs(sum(probs)-1)>1e-5):
        raise ContractError("FIVE_SESSION_PROBABILITIES")
    return probs

def input_snapshot(quotes,proof,anchor_ms):
    if proof.get("asof_ms")!=anchor_ms:
        raise ContractError("FIVE_SESSION_SOURCE_CUTOFF_CHANGED")
    feature=original_feature_snapshot(quotes,proof,anchor_ms,require_fresh=True)
    bars,receipts=original_closed_bars(quotes,anchor_ms)
    if feature["anchor_ms"]!=anchor_ms or bars[-1]["time"]*1000!=anchor_ms-BAR_MS:
        raise ContractError("FIVE_SESSION_ANCHOR_UNAVAILABLE")
    return {"feature":feature,"source_proof":proof,
        "base_mid":bars[-1]["midpoint"]["close"],
        "input_bar_receipts":receipts[-13:],
        "raw_original_prefixes_retained_at_source":True}

def _paths(root,slot):
    root=Path(root)
    return {kind:root/(kind+"-%04d.json"%slot) for kind in ("input","prediction","seal","commit","outcome")}

def persist_prediction(root,plan,slot,snapshot,probs,prior,model_sha256,clip_count,clock):
    anchor=plan["anchors"][slot]["anchor_ms"];deadline=anchor+INPUT_DEADLINE_MS
    p=_paths(root,slot)
    if not anchor<=clock()<=deadline:raise ContractError("FIVE_SESSION_INPUT_DEADLINE")
    write_once(p["input"],snapshot)
    at=clock()
    if not anchor<=at<=deadline:raise ContractError("FIVE_SESSION_INPUT_FSYNC_LATE")
    pred=stamp({"schema":SCHEMA+".prediction","plan_id":plan["plan_id"],"slot":slot,
        "anchor_ms":anchor,"created_at_ms":at,"source_asof_ms":anchor,
        "input_sha256":sha_file(p["input"]),"model_sha256":model_sha256,
        "model_probs":check_probs(probs),"prior_probs":check_probs(prior),
        "base_mid":snapshot["base_mid"],"clip_count":int(clip_count),
        "trade_authority":False},"prediction_id")
    write_once(p["prediction"],pred)
    pred_done=clock()
    if not at<=pred_done<=deadline:raise ContractError("FIVE_SESSION_PREDICTION_FSYNC_LATE")
    seal={"prediction_sha256":sha_file(p["prediction"]),
        "prediction_durable_at_ms":pred_done,"plan_id":plan["plan_id"],"slot":slot}
    write_once(p["seal"],seal)
    seal_done=clock()
    if not pred_done<=seal_done<=deadline:raise ContractError("FIVE_SESSION_SEAL_FSYNC_LATE")
    # This commit records the actual clock AFTER both prior files were fsynced.
    # Missing commit after a crash is never reconstructed retrospectively.
    commit={"plan_id":plan["plan_id"],"slot":slot,
        "prediction_sha256":sha_file(p["prediction"]),"seal_sha256":sha_file(p["seal"]),
        "prediction_and_seal_durable_at_ms":seal_done,
        "local_clock_receipt_not_external_timestamp_authority":True}
    write_once(p["commit"],commit)
    if clock()>deadline:raise ContractError("FIVE_SESSION_COMMIT_FSYNC_LATE")
    return pred

def verified_prediction(root,plan,slot,model_sha256,prior):
    p=_paths(root,slot);anchor=plan["anchors"][slot]["anchor_ms"]
    pred=verify_stamp(read_json(p["prediction"]),"prediction_id")
    seal=read_json(p["seal"]);commit=read_json(p["commit"])
    if (pred.get("plan_id")!=plan["plan_id"] or pred.get("slot")!=slot
        or pred.get("anchor_ms")!=anchor or pred.get("source_asof_ms")!=anchor
        or pred.get("model_sha256")!=model_sha256 or pred.get("prior_probs")!=prior
        or pred.get("trade_authority") is not False
        or pred.get("input_sha256")!=sha_file(p["input"])
        or not anchor<=pred["created_at_ms"]<=seal["prediction_durable_at_ms"]
            <=commit["prediction_and_seal_durable_at_ms"]<=anchor+INPUT_DEADLINE_MS
        or any(x.get("plan_id")!=plan["plan_id"] or x.get("slot")!=slot for x in (seal,commit))
        or seal.get("prediction_sha256")!=sha_file(p["prediction"])
        or commit.get("prediction_sha256")!=sha_file(p["prediction"])
        or commit.get("seal_sha256")!=sha_file(p["seal"])
        or not anchor<=p["commit"].stat().st_mtime_ns//1000000<=anchor+INPUT_DEADLINE_MS):
        raise ContractError("FIVE_SESSION_SAVED_PREDICTION_CHANGED_OR_LATE")
    check_probs(pred["model_probs"]);check_probs(pred["prior_probs"])
    snap=read_json(p["input"])
    if (snap["source_proof"]["asof_ms"]!=anchor or snap["feature"]["anchor_ms"]!=anchor
        or snap["base_mid"]!=pred["base_mid"]
        or snap["feature"]["quote_prefixes_digest"]!=digest(snap["source_proof"])
        or snap["feature"]["feature_names"]!=list(FEATURE_NAMES)):
        raise ContractError("FIVE_SESSION_SAVED_INPUT_CHANGED")
    return pred

def censor(plan,slot,at_ms,reason):
    return {"schema":SCHEMA+".outcome","plan_id":plan["plan_id"],"slot":slot,
        "anchor_ms":plan["anchors"][slot]["anchor_ms"],"at_ms":at_ms,
        "status":"CENSORED","reason":reason,"trade_authority":False}

def grade(plan,slot,pred,quotes,proof,now_ms):
    anchor=plan["anchors"][slot]["anchor_ms"];due=anchor+BAR_MS
    if now_ms<due:return None
    if now_ms>due+OUTCOME_GRACE_MS:return censor(plan,slot,now_ms,"OUTCOME_DEADLINE_MISSED")
    if proof.get("asof_ms")!=due:raise ContractError("FIVE_SESSION_OUTCOME_CUTOFF_CHANGED")
    bars,receipts=original_closed_bars(quotes,due)
    matches=[(b,r) for b,r in zip(bars,receipts) if b["time"]*1000==anchor]
    if len(matches)!=1:return None
    bar,receipt=matches[0]
    bp=(bar["midpoint"]["close"]/pred["base_mid"]-1)*10000
    target=bisect_right((-10,-3,-1,1,3,10),bp)
    def score(p):
        check_probs(p)
        return {"nll":-log(max(p[target],1e-12)),
            "brier":sum((x-int(i==target))**2 for i,x in enumerate(p)),
            "hit":max(range(7),key=lambda i:p[i])==target}
    return {"schema":SCHEMA+".outcome","plan_id":plan["plan_id"],"slot":slot,
        "anchor_ms":anchor,"at_ms":now_ms,"status":"OBSERVED","return_bps":bp,
        "actual_class":target,"outcome_close":bar["midpoint"]["close"],
        "outcome_receipt":receipt,"source_proof":proof,
        "prediction_id":pred["prediction_id"],"model":score(pred["model_probs"]),
        "prior":score(pred["prior_probs"]),"trade_authority":False}

def pending_actions(plan,now,predicted,recorded):
    buckets=[[],[],[],[]]
    for row in plan["anchors"]:
        slot=row["slot"];anchor=row["anchor_ms"]
        if slot in recorded:continue
        if slot not in predicted:
            if anchor<=now<=anchor+INPUT_DEADLINE_MS:buckets[0].append((slot,"PREDICT"))
            elif now>anchor+INPUT_DEADLINE_MS:buckets[2].append((slot,"CENSOR_INPUT"))
        else:
            due=anchor+BAR_MS
            if due<=now<=due+OUTCOME_GRACE_MS:buckets[1].append((slot,"GRADE"))
            elif now>due+OUTCOME_GRACE_MS:buckets[3].append((slot,"CENSOR_OUTCOME"))
    return sum(buckets,[])

def summarize(plan,outcomes):
    seen=set();observed=[];censored=0;sessions=[]
    for row in outcomes:
        i=row["slot"]
        if (type(i) is not int or not 0<=i<len(plan["anchors"]) or i in seen
            or row.get("plan_id")!=plan["plan_id"] or row.get("trade_authority") is not False
            or row.get("anchor_ms")!=plan["anchors"][i]["anchor_ms"]
            or row.get("status") not in ("OBSERVED","CENSORED")):
            raise ContractError("FIVE_SESSION_OUTCOME_IDENTITY")
        seen.add(i)
        if row["status"]=="OBSERVED":observed.append(row)
        else:censored+=1
    def scores(rows):
        return {key:{"nll":sum(r[key]["nll"] for r in rows)/len(rows) if rows else None,
                     "brier":sum(r[key]["brier"] for r in rows)/len(rows) if rows else None,
                     "hits":sum(r[key]["hit"] for r in rows)} for key in ("model","prior")}
    for s in plan["sessions"]:
        rows=[r for r in outcomes if plan["anchors"][r["slot"]]["session"]==s["session"]]
        obs=[r for r in rows if r["status"]=="OBSERVED"]
        sessions.append({"session":s["session"],"observed":len(obs),
            "censored":len(rows)-len(obs),"scores_on_observed_only":scores(obs)})
    return {"eligible_opportunities":len(plan["anchors"]),"recorded":len(seen),
        "observed":len(observed),"censored":censored,
        "pending":len(plan["anchors"])-len(seen),
        "scores_on_observed_only":scores(observed),"sessions":sessions,
        "coverage_is_not_model_or_economic_qualification":True}

def verify_outcome(root,plan,row,model_sha256,prior):
    slot=row["slot"];anchor=plan["anchors"][slot]["anchor_ms"]
    summarize(plan,[row])
    if row["status"]=="CENSORED":
        if not isinstance(row.get("reason"),str) or not row["reason"]:
            raise ContractError("FIVE_SESSION_CENSOR_REASON")
        return row
    pred=verified_prediction(root,plan,slot,model_sha256,prior)
    p=_paths(root,slot)
    receipt=row.get("outcome_receipt",{})
    if (not anchor+BAR_MS<=row["at_ms"]<=anchor+BAR_MS+OUTCOME_GRACE_MS
        or row.get("prediction_id")!=pred["prediction_id"]
        or row.get("prediction_sha256")!=sha_file(p["prediction"])
        or row.get("source_proof",{}).get("asof_ms")!=anchor+BAR_MS
        or receipt.get("bar_start_ms")!=anchor
        or receipt.get("bar_end_ms")!=anchor+BAR_MS
        or not isinstance(receipt.get("last_quote_receipt"),str)
        or len(receipt["last_quote_receipt"])!=64
        or receipt.get("max_known_at_ms",anchor+BAR_MS+1)>anchor+BAR_MS):
        raise ContractError("FIVE_SESSION_OUTCOME_RECEIPT_CHANGED")
    close=row.get("outcome_close")
    if type(close) not in (int,float) or not isfinite(close) or close<=0:
        raise ContractError("FIVE_SESSION_OUTCOME_PRICE")
    bp=(close/pred["base_mid"]-1)*10000;target=bisect_right((-10,-3,-1,1,3,10),bp)
    if row.get("return_bps")!=bp or row.get("actual_class")!=target:
        raise ContractError("FIVE_SESSION_OUTCOME_LABEL_CHANGED")
    for key,probs in (("model",pred["model_probs"]),("prior",pred["prior_probs"])):
        nll=-log(max(probs[target],1e-12))
        brier=sum((x-int(i==target))**2 for i,x in enumerate(probs))
        hit=max(range(7),key=lambda i:probs[i])==target
        score=row.get(key,{})
        if (score.get("nll")!=nll or score.get("brier")!=brier or score.get("hit") is not hit):
            raise ContractError("FIVE_SESSION_OUTCOME_SCORE_CHANGED")
    assessed=Path(root)/("assessment-%04d.json"%slot)
    if (row.get("assessment_sha256")!=sha_file(assessed)
        or row.get("assessment_durable_at_ms",anchor+BAR_MS+OUTCOME_GRACE_MS+1)>anchor+BAR_MS+OUTCOME_GRACE_MS):
        raise ContractError("FIVE_SESSION_OUTCOME_PERSISTENCE_CHANGED")
    original=read_json(assessed)
    durable=row["assessment_durable_at_ms"]
    expected={**original,"assessment_sha256":sha_file(assessed),
        "assessment_durable_at_ms":durable,"at_ms":durable}
    if (row!=expected or not anchor+BAR_MS<=original["at_ms"]<=durable
        or not anchor+BAR_MS<=assessed.stat().st_mtime_ns//1000000<=durable):
        raise ContractError("FIVE_SESSION_ASSESSMENT_CONTENT_CHANGED")
    return row
