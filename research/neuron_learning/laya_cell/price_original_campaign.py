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
REQUIRED_PINS=frozenset({
    "revised_bars","frozen_folds","model_weights","frozen_selection",
    "frozen_protocol","feature_module","original_bridge",
    "contract_module","runner","source_config"})


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
            type(plan.get("created_at_ms")) is not int or
            type(plan.get("start_ms")) is not int or
            plan["start_ms"]%SLOT_SPACING_MS!=0 or
            not plan["created_at_ms"]+HORIZON_MS<=plan["start_ms"]<plan["created_at_ms"]+2*SLOT_SPACING_MS+1 or
            plan.get("last_outcome_end_ms")!=plan["start_ms"]+SLOT_SPACING_MS*(SLOTS-1)+HORIZON_MS or
            plan.get("selection_from_year_fold")!=13 or
            plan.get("selected_family")!="tiny_mlp" or
            type(plan.get("pinned_sources")) is not dict or
            set(plan["pinned_sources"])!=REQUIRED_PINS or
            any(type(v) is not str or len(v)!=64 or
                any(c not in "0123456789abcdef" for c in v)
                for v in plan["pinned_sources"].values()) or
            plan.get("trade_authority") is not False or
            plan.get("model_promotion") is not False or
            plan.get("formal_laya_trial") is not False or
            plan.get("original_asof_prior_shadow_unchanged") is not True or
            plan.get("broker_orders_sent")!=0 or
            plan.get("historical_bar_source_is_revised") is not True or
            plan.get("live_source")!="ORIGINAL_VERIFIED_PAPER_QUOTES"):
        raise ContractError("ORIGINAL_CAMPAIGN_PLAN_CHANGED")
    check_probs(plan["training_prior_probs"])
    return plan


def create_plan(now_ms, pins, prior, normalized_feature_hash):
    if type(now_ms) is not int or now_ms<=0:
        raise ContractError("ORIGINAL_CAMPAIGN_CLOCK")
    if not isinstance(pins,dict) or set(pins)!=REQUIRED_PINS or any(
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
            type(feature.get("snapshot_id")) is not str or
            len(feature["snapshot_id"])!=64 or
            type(source_pin_digest) is not str or len(source_pin_digest)!=64 or
            type(feature.get("features")) is not list or
            len(feature["features"])!=24 or
            any(type(v) not in (float,int) or not isfinite(v) for v in feature["features"]) or
            feature.get("future_outcome_was_not_required") is not True or
            type(feature.get("asof_ms")) is not int or
            not anchor<=feature["asof_ms"]<=now_ms or
            type(feature.get("last_closed_bar_age_ms")) is not int or
            not 0<=feature["last_closed_bar_age_ms"]<=INPUT_GRACE_MS or
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
    if type(now_ms) is not int:
        raise ContractError("ORIGINAL_CAMPAIGN_OUTCOME_CLOCK")
    if now_ms<anchor+HORIZON_MS:
        return None
    if len(bars)!=len(receipts):
        raise ContractError("ORIGINAL_CAMPAIGN_BAR_RECEIPT_LENGTH")
    match=[(b,r) for b,r in zip(bars,receipts) if b["time"]*1000==anchor]
    base={"schema":OUTCOME_SCHEMA,"plan_id":plan["plan_id"],"slot":slot,
          "anchor_ms":anchor,"at_ms":now_ms,"prediction_id":pred["prediction_id"],
          "source_pin_digest":source_pin_digest,
          "automatic_trading":False,"broker_orders_sent":0,"model_promotion":False}
    if now_ms>anchor+HORIZON_MS+INPUT_GRACE_MS:
        # An old prediction must not receive a hindsight-success grade
        # after its committed original-source outcome deadline passed.
        return {**base,"status":"CENSORED","reason":"OUTCOME_COMMITTED_DEADLINE_MISSED"}
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



def verify_saved_outcome(plan, slot, outcome, sealed_prediction=None, prediction_file_sha256=None):
    """Revalidate persisted research result BEFORE accepting it after restart.

    The file bytes and the original causal quote receipt remain immutable.
    This proves score/probability consistency and local cross-file identity,
    not the historical oracle's truth or financial efficacy.
    """
    anchor=expected_anchor(plan,slot)
    state_summary(plan,[outcome])
    if sealed_prediction is None:
        if (outcome["status"]!="CENSORED" or
                sealed_prediction is not None or
                prediction_file_sha256 is not None or
                "prediction_id" in outcome or "prediction_sha256" in outcome or
                not outcome["reason"].startswith((
                    "MISSED_PRECOMMITTED_INPUT_SLOT",
                    "NO_STRICT_CAUSAL_INPUT:"))):
            raise ContractError("ORIGINAL_CAMPAIGN_CENSORED_WITHOUT_FORECAST_CHANGED")
        return outcome
    pred=sealed_prediction
    if (not isinstance(pred,dict) or
            pred.get("schema")!=PREDICTION_SCHEMA or
            pred.get("prediction_id")!=digest({k:v for k,v in pred.items() if k!="prediction_id"}) or
            pred.get("plan_id")!=plan["plan_id"] or
            pred.get("slot")!=slot or pred.get("anchor_ms")!=anchor or
            type(pred.get("created_at_ms")) is not int or
            not anchor<=pred["created_at_ms"]<=anchor+INPUT_GRACE_MS or
            type(pred.get("source_asof_ms")) is not int or
            not anchor<=pred["source_asof_ms"]<=pred["created_at_ms"] or
            type(pred.get("base_mid")) not in (int,float) or
            not isfinite(pred["base_mid"]) or pred["base_mid"]<=0 or
            type(pred.get("clip_count")) is not int or not 0<=pred["clip_count"]<=24 or
            pred.get("model_sha256")!=plan["pinned_sources"]["model_weights"] or
            pred.get("prior_probs")!=plan["training_prior_probs"] or
            pred.get("trading_authority") is not False or
            pred.get("model_promotion") is not False or
            pred.get("broker_orders_sent")!=0 or
            type(pred.get("source_snapshot_id")) is not str or
            len(pred["source_snapshot_id"])!=64 or
            type(pred.get("feature_vector_digest")) is not str or
            len(pred["feature_vector_digest"])!=64):
        raise ContractError("ORIGINAL_CAMPAIGN_SAVED_PREDICTION_CHANGED")
    check_probs(pred["model_probs"])
    check_probs(pred["prior_probs"])
    if (type(prediction_file_sha256) is not str or len(prediction_file_sha256)!=64 or
            outcome.get("prediction_sha256")!=prediction_file_sha256):
        raise ContractError("ORIGINAL_CAMPAIGN_PREDICTION_FILE_SHA_CHANGED")
    if "prediction_id" in outcome and outcome["prediction_id"]!=pred["prediction_id"]:
        raise ContractError("ORIGINAL_CAMPAIGN_PREDICTION_ID_CHANGED")
    if outcome["status"]=="CENSORED":
        if (outcome["at_ms"]<anchor+HORIZON_MS or
                outcome["reason"].startswith((
                    "MISSED_PRECOMMITTED_INPUT_SLOT","NO_STRICT_CAUSAL_INPUT:")) or
                "actual_class" in outcome or "model" in outcome or
                "train_frequency_prior" in outcome):
            raise ContractError("ORIGINAL_CAMPAIGN_CENSORED_WITH_FORECAST_CHANGED")
        return outcome

    due=anchor+HORIZON_MS
    if (not due<=outcome["at_ms"]<=due+INPUT_GRACE_MS or
            type(outcome.get("return_bps")) not in (int,float) or
            not isfinite(outcome["return_bps"]) or
            outcome["actual_class"]!=bisect_right(BIN_EDGES_BPS,outcome["return_bps"]) or
            outcome.get("reason") is not None or
            outcome.get("no_economic_alpha_or_prospective_power_claim") is not True or
            outcome.get("prediction_id")!=pred["prediction_id"] or
            type(outcome.get("source_pin_digest")) is not str or
            len(outcome["source_pin_digest"])!=64):
        raise ContractError("ORIGINAL_CAMPAIGN_PERSISTED_OUTCOME_CHANGED")
    target=outcome["actual_class"]
    for name,probs in (("model",pred["model_probs"]),
                       ("train_frequency_prior",pred["prior_probs"])):
        expected_brier=sum((v-float(k==target))**2 for k,v in enumerate(probs))
        expected_nll=-log(max(probs[target],1e-12))
        expected_hit=max(range(7),key=lambda k:probs[k])==target
        actual=outcome[name]
        if (abs(actual["brier"]-expected_brier)>1e-10 or
                abs(actual["nll"]-expected_nll)>1e-10 or
                actual["hit"] is not expected_hit):
            raise ContractError("ORIGINAL_CAMPAIGN_PERSISTED_SCORE_CHANGED")
    return outcome


def state_summary(plan,results):
    check_plan(plan)
    seen=set();observed=censored=0
    observed_scores={"model":{"brier":0.0,"nll":0.0,"hits":0},
                     "train_frequency_prior":{"brier":0.0,"nll":0.0,"hits":0}}
    for obj in results:
        slot=obj.get("slot")
        if (type(slot) is not int or not 0<=slot<SLOTS or slot in seen or
                obj.get("plan_id")!=plan["plan_id"] or
                obj.get("schema")!=OUTCOME_SCHEMA or
                type(obj.get("at_ms")) is not int or
                obj["at_ms"]<expected_anchor(plan,slot) or
                obj.get("status") not in ("OBSERVED","CENSORED") or
            obj.get("automatic_trading") is not False or
            obj.get("model_promotion") is not False or
            obj.get("broker_orders_sent")!=0 or
            (obj.get("anchor_ms")!=expected_anchor(plan,slot))):

            raise ContractError("ORIGINAL_CAMPAIGN_OUTCOME_IDENTITY")
        if obj["status"]=="OBSERVED":
            if (type(obj.get("actual_class")) is not int or not 0<=obj["actual_class"]<7
                    or type(obj.get("outcome_quote_receipt")) is not str
                    or len(obj["outcome_quote_receipt"])!=64
                    or obj.get("outcome_end_ms")!=expected_anchor(plan,slot)+HORIZON_MS
                    or type(obj.get("prediction_id")) is not str
                    or len(obj["prediction_id"])!=64):
                raise ContractError("ORIGINAL_CAMPAIGN_OBSERVED_RECEIPT")
            for key in ("model","train_frequency_prior"):
                score=obj.get(key)
                if (not isinstance(score,dict) or
                        type(score.get("brier")) not in (float,int) or
                        not isfinite(score["brier"]) or not 0<=score["brier"]<=2 or
                        type(score.get("nll")) not in (float,int) or
                        not isfinite(score["nll"]) or score["nll"]<0 or
                        type(score.get("hit")) is not bool):
                    raise ContractError("ORIGINAL_CAMPAIGN_OBSERVED_METRIC")
                observed_scores[key]["brier"]+=score["brier"]
                observed_scores[key]["nll"]+=score["nll"]
                observed_scores[key]["hits"]+=int(score["hit"])
        else:
            if type(obj.get("reason")) is not str or not obj["reason"]:
                raise ContractError("ORIGINAL_CAMPAIGN_CENSORED_REASON")
        seen.add(slot)
        observed+=obj["status"]=="OBSERVED"
        censored+=obj["status"]=="CENSORED"
    observed_means={
        key:{"mean_brier":values["brier"]/observed if observed else None,
             "mean_nll":values["nll"]/observed if observed else None,
             "top1_hits":values["hits"]}
        for key,values in observed_scores.items()
    }
    return {"observed":observed,"censored":censored,"recorded":len(seen),
            "minimum_88_of_90_possible":censored<=MAX_CENSORED,
            "scores_on_observed_only":observed_means,
            "no_favorable_window_selection":True,
            "research_source_coverage_only":True,
            "statistical_qualification":False,"financial_alpha_proven":False,
            "formal_laya_qualification":False,"trade_authority":False,
            "broker_orders_sent":0}
