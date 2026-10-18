"""Future *research-only* Laya ACT/ESCALATE training protocol v2.

This does NOT modify the current pinned resident_train OBJECTIVE, candidate
manifests, evaluator, model promotion, or Broker. It diagnoses the saturated
upstream ACT output and defines how a future *newly registered* training plan
can use genuine, balanced, already-observed correctness labels without
overweighting action CE or hallucinating missing ACT examples.
"""
from __future__ import annotations

import math
from collections import Counter

from .integrity import ContractError, digest

SCHEMA = "heptatrader.laya-resident-action-v2-research.v1"
MIN_TRAIN_ROWS = 16
MIN_EACH_LABEL = 4
MAX_TRAIN_ROWS = 4096
ACTION_WEIGHT_CAP = 0.1
MAX_ACTION_LOSS_FRACTION = 0.20
MAX_TRAIN_TEMPERATURE = 1_000_000.0
PAIRED_BATCH_BALANCE = "DISJOINT_ONE_ACT_ONE_ESCALATE_UNWEIGHTED_CE"

def _validated_rows(rows, *, cutoff_ms):
    if type(cutoff_ms) is not int or cutoff_ms<=0:
        raise ContractError("V2_ACTION_TRAIN_CUTOFF_REQUIRED")
    if not isinstance(rows, list) or not MIN_TRAIN_ROWS<=len(rows)<=MAX_TRAIN_ROWS:
        raise ContractError("V2_ACTION_TRAIN_ROW_COUNT")
    seen=set()
    for row in rows:
        if (not isinstance(row,dict) or
                type(row.get("row_id")) is not str or
                len(row["row_id"])!=64 or
                any(c not in "0123456789abcdef" for c in row["row_id"]) or
                row["row_id"] in seen or
                type(row.get("anchor_ms")) is not int or
                type(row.get("future_outcome_available_ms")) is not int or
                row["future_outcome_available_ms"]<=row["anchor_ms"] or
                row["future_outcome_available_ms"]>cutoff_ms or
                row["anchor_ms"]>=cutoff_ms or
                type(row.get("parent_correct")) is not bool):
            raise ContractError("V2_ACTION_CAUSAL_TRAINING_ROW_IDENTITY")
        seen.add(row["row_id"])
    return rows


def decide_train_only_action(rows, *, cutoff_ms, parent_action_logits):
    """Only truly observed frozen predecessor correctness supplies labels.

    Every input is already mature by the specified cutoff. The action head
    is *not* trained if either label lacks at least four independent, unique
    rows. Price updates can proceed separately with their original objective.
    """
    import torch
    rows=_validated_rows(rows,cutoff_ms=cutoff_ms)
    if (not torch.is_tensor(parent_action_logits) or
            tuple(parent_action_logits.shape)!=(len(rows),2) or
            not parent_action_logits.is_floating_point() or
            not torch.isfinite(parent_action_logits).all()):
        raise ContractError("V2_ACTION_PARENT_LOGIT_POPULATION")
    counts=Counter(int(not r["parent_correct"]) for r in rows)
    admits=(counts[0]>=MIN_EACH_LABEL and counts[1]>=MIN_EACH_LABEL)
    # Freeze one temperature for ALL future batches. Never choose it using
    # a validation or future score. Training-only P90 of absolute logit gap.
    with torch.no_grad():
        margins=sorted(abs(float(v)) for v in (
            parent_action_logits[:,0]-parent_action_logits[:,1]).detach().cpu())
    scale=max(1.0,margins[min(len(margins)-1,math.ceil(.9*len(margins))-1)])
    if not math.isfinite(scale) or scale>MAX_TRAIN_TEMPERATURE:
        raise ContractError("V2_ACTION_UNCALIBRATABLE_LOGIT_RANGE")
    freeze={"schema":SCHEMA,"train_cutoff_ms":cutoff_ms,
            "training_rows":len(rows),
            "labels":{"ACT_correct":counts[0],"ESCALATE_wrong":counts[1]},
            "training_row_ids_digest":digest([r["row_id"] for r in rows]),
            "action_temperature_training_only":scale if admits else None,
            "training_p90_abs_action_logit_margin":scale,
            "action_optimizer_admitted":admits,
            "action_refusal_reason":None if admits else "NO_BOTH_TRUE_ACTION_LABELS",
            "price_learning_not_disallowed":True,
            "raw_pretrained_action_logit_interface_unchanged":True,
            "action_head_trade_permission":False,
            "model_promotion":False,"broker_orders_sent":0}
    freeze["profile_id"]=digest(freeze)
    return freeze


def _check_profile(profile):
    if (not isinstance(profile,dict) or profile.get("schema")!=SCHEMA or
            profile.get("profile_id")!=digest({k:v for k,v in profile.items() if k!="profile_id"}) or
            profile.get("model_promotion") is not False or
            profile.get("action_head_trade_permission") is not False or
            profile.get("price_learning_not_disallowed") is not True or
            profile.get("raw_pretrained_action_logit_interface_unchanged") is not True or
            type(profile.get("action_optimizer_admitted")) is not bool):
        raise ContractError("V2_ACTION_FROZEN_PROFILE_CHANGED")
    if profile.get("batch_balance_strategy") not in (None, PAIRED_BATCH_BALANCE):
        raise ContractError("ACTION_BATCH_BALANCE_PROFILE_CHANGED")
    counts=profile["labels"]
    if (type(counts.get("ACT_correct")) is not int or
            type(counts.get("ESCALATE_wrong")) is not int or
            counts["ACT_correct"]+counts["ESCALATE_wrong"]!=profile.get("training_rows")):
        raise ContractError("V2_ACTION_LABEL_COUNT_CHANGED")
    if profile["action_optimizer_admitted"]:
        t=profile.get("action_temperature_training_only")
        if (not isinstance(t,(float,int)) or not math.isfinite(t) or not 1<=t<=MAX_TRAIN_TEMPERATURE
                or min(counts.values())<MIN_EACH_LABEL):
            raise ContractError("V2_ACTION_UNSAFE_TEMPERATURE")
    else:
        if (profile.get("action_temperature_training_only") is not None or
                profile.get("action_refusal_reason")!="NO_BOTH_TRUE_ACTION_LABELS"):
            raise ContractError("V2_ACTION_REFUSAL_CHANGED")
    return profile



def stratified_action_pairs(rows, profile):
    """Disjoint genuine ACT/ESCALATE pairs, no synthetic/future labels.

    Only paired rows qualify for balanced ACTION gradients. Any excess
    majority-class rows remain eligible for the separate PRICE objective.
    """
    p=_check_profile(profile)
    if not p["action_optimizer_admitted"]:
        return []
    if (not isinstance(rows,list) or len(rows)!=p["training_rows"] or
            digest([r["row_id"] for r in rows])!=p["training_row_ids_digest"]):
        raise ContractError("V2_ACTION_STRATIFIED_TRAINING_ROWS_CHANGED")
    act=[i for i,r in enumerate(rows) if r["parent_correct"] is True]
    escalate=[i for i,r in enumerate(rows) if r["parent_correct"] is False]
    if (len(act)!=p["labels"]["ACT_correct"] or
            len(escalate)!=p["labels"]["ESCALATE_wrong"]):
        raise ContractError("V2_ACTION_STRATIFIED_LABELS_CHANGED")
    pairs=list(zip(act,escalate))
    flat=[j for pair in pairs for j in pair]
    if len(flat)!=len(set(flat)) or len(pairs)<MIN_EACH_LABEL:
        raise ContractError("V2_ACTION_STRATIFIED_POPULATION")
    return pairs

def balanced_action_loss(action_logits, parent_correct, primary_price_loss, profile):
    """Causal two-class-balanced, temperature-scaled, numerically capped CE.

    For an all-ESCALATE historical batch, return zero action gradients.
    The caller still backprops its PRICE loss, not this action loss.
    """
    import torch
    from torch.nn import functional as F
    p=_check_profile(profile)
    if (not torch.is_tensor(action_logits) or action_logits.ndim!=2 or
            action_logits.shape[-1]!=2 or
            not action_logits.is_floating_point() or not torch.isfinite(action_logits).all() or
            not torch.is_tensor(parent_correct) or
            tuple(parent_correct.shape)!=(len(action_logits),) or
            parent_correct.dtype!=torch.bool or
            not torch.is_tensor(primary_price_loss) or primary_price_loss.ndim!=0 or
            not torch.isfinite(primary_price_loss) or primary_price_loss.detach().item()<0):
        raise ContractError("V2_ACTION_LOSS_INPUT")
    if not p["action_optimizer_admitted"]:
        return action_logits.sum()*0.0
    targets=(~parent_correct).long()
    counts=torch.bincount(targets,minlength=2)
    if torch.any(counts==0):
        # Individual minibatches may have one label; use frozen global
        # weights, but forbid a false two-class claim at batch level.
        return action_logits.sum()*0.0
    total=p["training_rows"]
    weights=torch.tensor([
        total/(2*p["labels"]["ACT_correct"]),
        total/(2*p["labels"]["ESCALATE_wrong"])],
        device=action_logits.device,dtype=action_logits.dtype)
    if p.get("batch_balance_strategy") == PAIRED_BATCH_BALANCE:
        # A v3 batch is already one genuine ACT and one genuine ESCALATE.
        # Applying original-population class weights here would balance twice.
        if counts.tolist() != [1, 1]:
            raise ContractError("ACTION_DISJOINT_PAIR_REQUIRED")
        weights = None
    # Runtime logits need not be clipped or modified at inference; this
    # is the loss of a future, separately versioned action calibration.
    scaled=action_logits/p["action_temperature_training_only"]
    ce=F.cross_entropy(scaled,targets,weight=weights)
    # Numeric dominance cap with detached factors. Do NOT replace genuine
    # price gradients or silently reweight labels using future observations.
    with torch.no_grad():
        bound=(MAX_ACTION_LOSS_FRACTION*primary_price_loss.detach()/
               ce.detach().clamp(min=1e-12))
        coefficient=torch.minimum(ce.detach().new_tensor(ACTION_WEIGHT_CAP),bound)
    return ce*coefficient


def research_action_recommendation(profile, prob_parent_correct):
    """Informational only, never trading authority; unsupported -> escalate."""
    _check_profile(profile)
    if (type(prob_parent_correct) not in (float,int) or
            not math.isfinite(prob_parent_correct) or
            not 0<=prob_parent_correct<=1):
        raise ContractError("V2_ACTION_PROBABILITY_INPUT")
    if not profile["action_optimizer_admitted"]:
        return {"signal":"ESCALATE_ONLY_NO_BALANCED_TRAINING",
                "model_or_trade_permission":False}
    return {"signal":"RESEARCH_ONLY_ACT_CANDIDATE" if prob_parent_correct>=5/6
                     else "ESCALATE_RESEARCH_ONLY",
            "model_or_trade_permission":False}



def v2_action_inference(action_logits, profile):
    """Numerical research-only output for a FUTURE explicitly versioned model.

    The exact frozen train-only temperature that defines the v2 ACTION
    training loss must also define its inference probabilities. The v1
    resident_predict/raw action head remains completely untouched.
    One-class training, nonfinite logits and major OOD margins abstain.
    Temperature normalization alone is NOT held-out probability calibration.
    """
    import torch
    p=_check_profile(profile)
    if (not torch.is_tensor(action_logits) or
            action_logits.ndim!=2 or not 1<=len(action_logits)<=4096 or
            action_logits.shape[-1]!=2 or
            not action_logits.is_floating_point() or
            not torch.isfinite(action_logits).all()):
        raise ContractError("V2_ACTION_INFERENCE_BAD_LOGITS")
    n=len(action_logits)
    if not p["action_optimizer_admitted"]:
        return {"schema":"heptatrader.laya-action-v2-inference-research.v1",
                "status":"NO_TRAINING_SUPPORT_ALL_ESCALATE",
                "frozen_profile_id":p["profile_id"],
                "training_only_temperature":None,
                "action_correct_probs":None,
                "recommendations":["ESCALATE_ONLY_NO_BALANCED_TRAINING"]*n,
                "temperature_scaling_does_not_prove_calibration":True,
                "model_or_trade_permission":False}
    t=p["action_temperature_training_only"]
    # Difference in float64 avoids overflow from otherwise finite float32
    # upstream logit extremes. A dramatic training-vs-inference shift is a
    # reason to abstain, not to emit fake 100%-confident ACT.
    z=action_logits.detach().to(torch.float64)
    margins=(z[:,0]-z[:,1]).abs()
    too_far=(margins>10*t).tolist()
    probs=torch.softmax(z/t,dim=-1)[:,0].tolist()
    suggestions=[]
    valid=[]
    for probability,uncertain in zip(probs,too_far):
        if uncertain:
            valid.append(None)
            suggestions.append("ESCALATE_OOD_ACTION_LOGIT_SHIFT")
        else:
            valid.append(probability)
            suggestions.append(research_action_recommendation(p,probability)["signal"])
    return {"schema":"heptatrader.laya-action-v2-inference-research.v1",
            "status":"FROZEN_TRAIN_TEMPERATURE_RESEARCH_ONLY",
            "frozen_profile_id":p["profile_id"],
            "training_only_temperature":t,
            "action_correct_probs":valid,
            "recommendations":suggestions,
            "ood_abstentions":sum(bool(i) for i in too_far),
            "temperature_scaling_does_not_prove_calibration":True,
            "model_or_trade_permission":False}
