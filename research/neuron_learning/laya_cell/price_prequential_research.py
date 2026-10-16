"""As-of-time online seven-bin priors: historical research only.

An online update may use a label only after its actual recorded evaluation
timestamp, which must follow the future quote outcome. This is prequential,
not a fixed pretrained model's untouched test-label evaluation.
"""
from __future__ import annotations
from datetime import datetime, timezone
import math
from pathlib import Path

from .action_quality_research import collect_closed, chronological_days, probabilities
from .price_quality_research import prior, score, CLASSES
from .integrity import ContractError, digest, read_json, sha_file

SCHEMA = "heptatrader.price-prequential-research.v1"
HALF_LIFE_HOURS = (3, 6, 12, 24, 48, 0)  # 0 means cumulative forever.
BACKOFF_STRENGTH = (1., 4., 16., 64.)
MS_HOUR = 3600000

def collect_available(root):
    rows, source = collect_closed(root)
    lookup = {(r["plan_id"], r["anchor_ms"]): r for r in rows}
    if len(lookup) != len(rows):
        raise ContractError("ONLINE_DUPLICATE_FORECAST")
    times = {}
    for file in sorted(Path(root).glob("*/outcome-*.json")):
        o = read_json(file)
        if o.get("status") != "OBSERVED":
            continue
        key = (o.get("plan_id"), o.get("anchor_ms"))
        if key not in lookup or key in times:
            raise ContractError("ONLINE_OBSERVATION_IDENTITY")
        event = o.get("outcome_ms")
        available = o.get("evaluated_at_ms")
        anchor = o.get("anchor_ms")
        if (type(event) is not int or type(available) is not int or
                type(anchor) is not int or event < anchor + 300000 or
                available < event or available < anchor):
            raise ContractError("ONLINE_OUTCOME_NOT_CAUSALLY_AVAILABLE")
        # The hash was checked by collect_closed and is checked again here.
        if sha_file(file) is None:
            raise ContractError("ONLINE_OUTCOME_HASH")
        times[key] = available
    if set(times) != set(lookup):
        raise ContractError("ONLINE_MISSING_AVAILABILITY_RECEIPT")
    for r in rows:
        r["label_available_at_ms"] = times[(r["plan_id"], r["anchor_ms"])]
    return rows, source

def asof_prior(history, at_ms, global_prior, half_life_hours, strength):
    if (type(half_life_hours) is not int or type(strength) is not float or
            half_life_hours not in HALF_LIFE_HOURS or strength not in BACKOFF_STRENGTH):
        raise ContractError("ONLINE_UNREGISTERED_PARAMETERS")
    probabilities(global_prior,CLASSES)
    if type(at_ms) is not int:
        raise ContractError("ONLINE_INVALID_ASOF")
    counts = [strength * x for x in global_prior]
    admitted = 0
    for r in history:
        available = r.get("label_available_at_ms")
        anchor = r.get("anchor_ms")
        target = r.get("target_class")
        if (type(available) is not int or type(anchor) is not int or
                type(target) is not int or not 0 <= target < CLASSES or
                available < anchor + 300000):
            raise ContractError("ONLINE_HISTORY_RECEIPT_INVALID")
        if available > at_ms or anchor >= at_ms:
            continue
        age = at_ms - available
        weight = (1.0 if half_life_hours == 0
                  else math.exp(-math.log(2) * age/(half_life_hours*MS_HOUR)))
        counts[target] += weight
        admitted += 1
    total = sum(counts)
    if not total > 0 or not math.isfinite(total):
        raise ContractError("ONLINE_NORM_INVALID")
    return list(probabilities([v/total for v in counts],CLASSES)),admitted

def score_asof(target_rows, historical_labels, global_prior, half_life, strength):
    if not target_rows:
        raise ContractError("ONLINE_NO_TEST_ROWS")
    predict_rows = []
    admitted_counts = []
    for r in target_rows:
        # history includes all rows, but asof_prior must NEVER take future labels.
        q,admitted = asof_prior(historical_labels,r["anchor_ms"],global_prior,half_life,strength)
        predict_rows.append({"target_class":r["target_class"], "candidate_probabilities":q})
        admitted_counts.append(admitted)
    v = score(predict_rows,"candidate")
    v["first_asof_label_count"] = admitted_counts[0]
    v["last_asof_label_count"] = admitted_counts[-1]
    v["prequential_updates_from_later_labels_allowed_only_when_past"] = True
    return v

def experiment(root):
    rows, source = collect_available(root)
    train, validation, test = chronological_days(rows)
    global_prior = prior(train,.5)
    # Validation predictions incorporate already-mature historical test-independent
    # labels, while parameters themselves are selected using validation only.
    candidate_history = train + validation + test
    def key(h,s):
        return "cumulative" if h==0 else str(h)+"h" + "_backoff_"+str(s)
    # Explicit static anchor; available through all later days.
    baseline_val = score(validation,"constant",constant=global_prior["weights"])
    val = {"train_only_static_prior":baseline_val}
    for h in HALF_LIFE_HOURS:
        for s in BACKOFF_STRENGTH:
            name=("cumulative" if h==0 else str(h)+"h")+"_backoff_"+str(s)
            val[name] = score_asof(validation,candidate_history,global_prior["weights"],h,s)
    names = list(val)
    selected = min(names,key=lambda name:(val[name]["brier"],names.index(name)))
    test_scores = {"raw_candidate":score(test,"candidate"),
        "raw_parent":score(test,"parent"),
        "train_only_static_prior":score(test,"constant",constant=global_prior["weights"])}
    if selected != "train_only_static_prior":
        half,s = selected.split("_backoff_")
        hours = 0 if half=="cumulative" else int(half[:-1])
        chosen = float(s)
        test_scores["validation_selected_prequential"] = score_asof(
            test,candidate_history,global_prior["weights"],hours,chosen)
        params={"half_life_hours":hours,"backoff_strength":chosen}
    else:
        test_scores["validation_selected_prequential"] = test_scores["train_only_static_prior"]
        params=None
    report = {"schema":SCHEMA,"source":source,
        "split_days":{"train":sorted({r["utc_day"] for r in train}),
                       "validation":sorted({r["utc_day"] for r in validation}),
                       "test":sorted({r["utc_day"] for r in test})},
        "counts":{"training":len(train),"validation":len(validation),"test":len(test)},
        "model_grid_fixed_before_test":{
            "half_life_hours":list(HALF_LIFE_HOURS),"backoff_strength":list(BACKOFF_STRENGTH),
            "training_global_smoothing":0.5},
        "selection_rule":"LOWEST_CAUSAL_VALIDATION_BRIER_STATIC_TIEBREAK",
        "validation_scores":val,"validation_selected":selected,
        "selection_parameters":params,"global_training_prior":global_prior,
        "test_scores":test_scores,
        "claims":{"retrospective_prequential_only":True,
            "evaluation_uses_only_available_past_outcomes":True,
            "no_trade_authority":True,"no_model_promotion":True,
            "no_prospective_frozen_candidate_efficacy":True,
            "not_randomized":True}}
    report["report_id"] = digest(report)
    return report
