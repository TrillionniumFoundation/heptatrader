"""Immutable original PAPER quote Tiny MLP research campaign contracts.

This is only original-source probability forecasting and scoring. No order,
trade, model promotion, user authority, online fitting, or modification to the
independent already-running as-of prior Shadow experiment.
"""
from __future__ import annotations

from bisect import bisect_right
from math import isfinite, log
from .dataset import BIN_EDGES_BPS
from .integrity import ContractError, digest

SCHEMA="heptatrader.laya-original-price-campaign-90.v1"
PREDICTION_SCHEMA="heptatrader.laya-original-price-precommit.v1"
OUTCOME_SCHEMA="heptatrader.laya-original-price-outcome.v1"
SLOTS=90
HORIZON_MS=300000
SLOT_SPACING_MS=300000
INPUT_GRACE_MS=60000
MIN_OBSERVED=88
MAX_CENSORED=2


def check_probs(p):
    if (not isinstance(p,list) or len(p)!=7 or
            any(type(x) not in (float,int) or not isfinite(x) or not 0<=x<=1 for x in p) or
            abs(sum(p)-1)>1e-5):
        raise ContractError("ORIGINAL_CAMPAIGN_PROBABILITIES")
    return p


def check_plan(plan):
    if (plan.get("schema")!=SCHEMA or
            plan.get("plan_id")!=digest({k:v for k,v in plan.items() if k!="plan_id"}) or
            plan.get("slots")!=SLOTS or plan.get("horizon_ms")!=HORIZON_MS or
            plan.get("slot_spacing_ms")!=SLOT_SPACING_MS or
            plan.get("input_grace_ms")!=INPUT_GRACE_MS or
            plan.get("minimum_observed")!=MIN_OBSERVED or
            plan.get("maximum_censored")!=MAX_CENSORED or
            type(plan.get("start_ms")) is not int or
            plan["start_ms"]%SLOT_SPACING_MS!=0 or
            plan.get("trade_authority") is not False or
            plan.get("model_promotion") is not False or
            plan.get("historical_bar_source_is_revised") is not True or
            plan.get("live_source")!="ORIGINAL_VERIFIED_PAPER_QUOTES"):
        raise ContractError("ORIGINAL_CAMPAIGN_PLAN_CHANGED")
    check_probs(plan["training_prior_probs"])
    return plan


def create_plan(now_ms, pins, prior, normalized_feature_hash):
    if type(now_ms) is not int or now_ms<=0:
        raise ContractError("ORIGINAL_CAMPAIGN_CLOCK")
    if not isinstance(pins,dict) or len(pins)<8 or any(
            type(v) is not str or len(v)!=64 or any(x not in "0123456789abcdef" for x in v)
            for v in pins.values()):
        raise ContractError("ORIGINAL_CAMPAIGN_PINS")
    if type(normalized_feature_hash) is not str or len(normalized_feature_hash)!=64:
        raise ContractError("ORIGINAL_CAMPAIGN_NORMALIZATION_HASH")
    # Start no sooner than the next full five-minute bar after registering.
    start=((now_ms+HORIZON_MS+SLOT_SPACING_MS-1)//SLOT_SPACING_MS)*SLOT_SPACING_MS
    data={"schema":SCHEMA,"created_at_ms":now_ms,"start_ms":start,
          "slots":SLOTS,"slot_spacing_ms":SLOT_SPACING_MS,
          "horizon_ms":HORIZON_MS,"input_grace_ms":INPUT_GRACE_MS,
          "last_outcome_end_ms":start+(SLOTS-1)*SLOT_SPACING_MS+HORIZON_MS,
          "minimum_observed":MIN_OBSERVED,"maximum_censored":MAX_CENSORED,
          "live_source":"ORIGINAL_VERIFIED_PAPER_QUOTES",
          "historical_bar_source_is_revised":True,
          "selection_from_year_fold":13,"selected_family":"tiny_mlp",
          "pinned_sources":pins,"train_only_normalization_sha256":normalized_feature_hash,
          "training_prior_probs":list(check_probs(prior)),
          "model_promotion":False,"formal_laya_trial":False,
          "trade_authority":False,"broker_orders_sent":0,
          "original_asof_prior_shadow_unchanged":True}
    data["plan_id"]=digest(data)
    return check_plan(data)


def expected_anchor(plan,slot):
    check_plan(plan)
    if type(slot) is not int or not 0<=slot<SLOTS:
        raise ContractError("ORIGINAL_CAMPAIGN_SLOT")
    return plan["start_ms"]+slot*SLOT_SPACING_MS


def prediction(plan,slot,feature,model_probs,now_ms,base_mid,source_pin_digest,clip_count):
    anchor=expected_anchor(plan,slot)
    if (type(now_ms) is not int or now_ms<anchor or now_ms>anchor+INPUT_GRACE_MS or
            type(feature.get("anchor_ms")) is not int or feature["anchor_ms"]!=anchor or
            feature.get("source_kind")!="ORIGINAL_VERIFIED_PAPER_QUOTES" or
            feature.get("original_event_ticks_not_revised_ib_bars") is not True or
            feature.get("trade_authority") is not False or
            feature.get("snapshot_id") is None or
            feature.get("future_outcome_was_not_required") is not True or
            feature.get("asof_ms")>now_ms or feature.get("last_closed_bar_age_ms")>INPUT_GRACE_MS or
            type(base_mid) not in (float,int) or not isfinite(base_mid) or base_mid<=0 or
            type(clip_count) is not int or not 0<=clip_count<=24):
        raise ContractError("ORIGINAL_CAMPAIGN_NONCAUSAL_INPUT")
    data={"schema":PREDICTION_SCHEMA,"plan_id":plan["plan_id"],"slot":slot,
          "anchor_ms":anchor,"created_at_ms":now_ms,"source_asof_ms":feature["asof_ms"],
          "source_snapshot_id":feature["snapshot_id"],
          "source_prefixes_digest":source_pin_digest,
          "feature_vector_digest":digest(feature["features"]),
          "base_mid":float(base_mid),
          "model_probs":list(check_probs(model_probs)),
          "prior_probs":list(check_probs(plan["training_prior_probs"])),
          "clip_count":clip_count,"model_sha256":plan["pinned_sources"]["model_weights"],
          "trading_authority":False,"model_promotion":False,"broker_orders_sent":0}
    data["prediction_id"]=digest(data)
    return data


def grade(plan,slot,pred,bars,receipts,now_ms,source_pin_digest):
    anchor=expected_anchor(plan,slot)
    if (pred.get("schema")!=PREDICTION_SCHEMA or pred.get("plan_id")!=plan["plan_id"] or
            pred.get("slot")!=slot or pred.get("anchor_ms")!=anchor or
            pred.get("prediction_id")!=digest({k:v for k,v in pred.items() if k!="prediction_id"}) or
            type(pred.get("created_at_ms")) is not int or not anchor<=pred["created_at_ms"]<=anchor+INPUT_GRACE_MS or
            pred.get("model_sha256")!=plan["pinned_sources"]["model_weights"] or
            pred.get("prior_probs")!=plan["training_prior_probs"] or
            pred.get("trading_authority") is not False or
            pred.get("model_promotion") is not False or
            pred.get("broker_orders_sent")!=0):
        raise ContractError("ORIGINAL_CAMPAIGN_PREDICTION_CHANGED")
    if type(now_ms) is not int or now_ms<anchor+HORIZON_MS:
        return None
    if len(bars)!=len(receipts):
        raise ContractError("ORIGINAL_CAMPAIGN_BAR_RECEIPT_LENGTH")
    match=[(b,r) for b,r in zip(bars,receipts) if b["time"]*1000==anchor]
    base={"schema":OUTCOME_SCHEMA,"plan_id":plan["plan_id"],"slot":slot,
          "anchor_ms":anchor,"at_ms":now_ms,"prediction_id":pred["prediction_id"],
          "source_pin_digest":source_pin_digest,
          "automatic_trading":False,"broker_orders_sent":0,"model_promotion":False}
    if len(match)!=1:
        if now_ms < anchor+HORIZON_MS+INPUT_GRACE_MS:return None
        return {**base,"status":"CENSORED","reason":"NEXT_ORIGINAL_BAR_NOT_STRICTLY_COMPLETE"}
    bar,receipt=match[0]
    close=bar.get("midpoint",{}).get("close")
    if (type(close) not in (float,int) or not isfinite(close) or close<=0 or
            receipt.get("bar_start_ms")!=anchor or
            receipt.get("bar_end_ms")!=anchor+HORIZON_MS or
            type(receipt.get("last_quote_receipt")) is not str or
            len(receipt["last_quote_receipt"])!=64):
        raise ContractError("ORIGINAL_CAMPAIGN_RESULT_PRICE_OR_RECEIPT")
    bp=(close/pred["base_mid"]-1)*10000
    target=bisect_right(BIN_EDGES_BPS,bp)
    def metric(probs):
        check_probs(probs)
        return {"brier":sum((x-float(i==target))**2 for i,x in enumerate(probs)),
                "nll":-log(max(probs[target],1e-12)),
                "hit":max(range(7),key=lambda i:probs[i])==target}
    return {**base,"status":"OBSERVED","reason":None,"outcome_end_ms":anchor+HORIZON_MS,
            "actual_class":target,"return_bps":bp,
            "outcome_quote_receipt":receipt["last_quote_receipt"],
            "model":metric(pred["model_probs"]),"train_frequency_prior":metric(pred["prior_probs"]),
            "no_economic_alpha_or_prospective_power_claim":True}


def state_summary(plan,results):
    check_plan(plan)
    seen=set();observed=censored=0
    for obj in results:
        slot=obj.get("slot")
        if (type(slot) is not int or not 0<=slot<SLOTS or slot in seen or
                obj.get("plan_id")!=plan["plan_id"] or
                obj.get("status") not in ("OBSERVED","CENSORED")):
            raise ContractError("ORIGINAL_CAMPAIGN_OUTCOME_IDENTITY")
        seen.add(slot)
        observed+=obj["status"]=="OBSERVED"
        censored+=obj["status"]=="CENSORED"
    return {"observed":observed,"censored":censored,"recorded":len(seen),
            "minimum_88_of_90_possible":censored<=MAX_CENSORED,
            "research_source_coverage_only":True,
            "formal_laya_qualification":False,"trade_authority":False,
            "broker_orders_sent":0}
