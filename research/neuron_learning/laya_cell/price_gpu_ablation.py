"""Causal, fixed-cohort Pocket4 GPU price-head comparison helpers.

Research only. Formal trials, broker orders, trade permissions and frozen
production objective are not part of this module.
"""
from __future__ import annotations
from bisect import bisect_right
from collections import Counter
from math import isfinite, log
from .integrity import ContractError, digest
from .resident_row_accounting import row_id

TRAIN_END_OFFSET_MS = 35 * 60_000
VALID_START_OFFSET_MS = 45 * 60_000
VALID_END_OFFSET_MS = 60 * 60_000
TEST_START_OFFSET_MS = 70 * 60_000
HORIZON_MS = 300_000
COHORT_PER_STAGE = 32
ARMS = ("original_action_weight_001", "price_only_action_weight_0")
ALPHA = 0.5  # fixed prior; do not search it on test day
MIN_INDEPENDENT_TEST_HORIZONS = 20


def _spread(rows, count):
    if len(rows) < count:
        raise ContractError("GPU_ABLATION_INSUFFICIENT_CAUSAL_SAMPLES")
    return [rows[i * len(rows) // count] for i in range(count)]


def cohort(rows, dataset_samples):
    five = sorted((r for r in rows if r.get("horizon_sec") == 300),
                  key=lambda r: r["anchor_ms"])
    anchors = [r["anchor_ms"] for r in five]
    if len(five) != len(set(anchors)) or len(five) < 3 * COHORT_PER_STAGE:
        raise ContractError("GPU_ABLATION_SAMPLE_IDENTITIES_OR_CAPACITY")
    starts = anchors[0]
    train_end = starts + TRAIN_END_OFFSET_MS
    valid_start = starts + VALID_START_OFFSET_MS
    valid_end = starts + VALID_END_OFFSET_MS
    test_start = starts + TEST_START_OFFSET_MS
    if not (train_end + HORIZON_MS < valid_start and
            valid_end + HORIZON_MS < test_start):
        raise ContractError("GPU_ABLATION_NO_CAUSAL_PURGE")
    training = _spread([r for r in five if r["anchor_ms"]+HORIZON_MS <= train_end],
                       COHORT_PER_STAGE)
    validation = _spread([r for r in five if valid_start <= r["anchor_ms"] < valid_end],
                         COHORT_PER_STAGE)
    test = _spread([r for r in five if r["anchor_ms"] >= test_start],
                   COHORT_PER_STAGE)
    samples = {sample["sample_id"]: sample for sample in dataset_samples}
    if len(samples) != len(dataset_samples):
        raise ContractError("GPU_ABLATION_DUPLICATE_SAMPLE_ID")
    for item in training:
        sample = samples[item["sample_id"]]
        receipt = sample["outcome_receipts"][0]
        if (sample["anchor_ms"] != item["anchor_ms"] or
                sample["known_at_ms"] > train_end or sample["known_at_ms"] > sample["anchor_ms"] + 60_000 or
                receipt.get("available_at_ms",train_end+1) > train_end or
                receipt.get("observed_at_ms",train_end+1) > train_end):
            raise ContractError("GPU_ABLATION_TRAIN_FUTURE_LABEL")
    populations = (training, validation, test)
    ids = [row_id(r) for block in populations for r in block]
    if len(set(ids)) != len(ids):
        raise ContractError("GPU_ABLATION_OVERLAPPING_SAMPLE_ID")
    return {"schema":"heptatrader.laya-price-gpu-cohort.v1",
            "selection":"FIXED_TIME_PURGE_AND_EVEN_SPREAD_WITHIN_BLOCK_V1",
            "pretrained_parent_frozen":True, "training_label_cutoff_ms":train_end,
            "training":training,"validation":validation,"test":test,
            "holdout_input_after_training_ms":valid_start,
            "test_input_after_validation_ms":test_start,
            "anchor_bounds":{name:[values[0]["anchor_ms"],values[-1]["anchor_ms"]]
                             for name,values in zip(("training","validation","test"),populations)},
            "row_identity":{"training":[row_id(r) for r in training],
                            "validation":[row_id(r) for r in validation],
                            "test":[row_id(r) for r in test]},
            "no_future_labels_in_optimizer":True, "formal_trial_registered":False,
            "no_model_promotion":True, "trading_authority":False}


def independent_count(rows):
    last = -1
    seen = 0
    for row in sorted(rows,key=lambda r:r["anchor_ms"]):
        if row["anchor_ms"] >= last + HORIZON_MS:
            last = row["anchor_ms"]
            seen += 1
    return seen


def score(probs, labels):
    if not probs or len(probs) != len(labels):
        raise ContractError("GPU_ABLATION_SCORE_SHAPE")
    brier = nll = 0.
    hits = 0
    predicted = Counter()
    observed = Counter()
    for p,y in zip(probs,labels):
        if (not isinstance(p,list) or len(p) != 7 or
                any(type(v) not in (int,float) or not isfinite(v) or not 0 <= v <= 1 for v in p) or
                abs(sum(p)-1) > 1e-5 or type(y) is not int or not 0 <= y < 7):
            raise ContractError("GPU_ABLATION_NONFINITE_OR_UNKNOWN_PROBS")
        prediction=max(range(7),key=lambda i:p[i])
        predicted[prediction]+=1
        observed[y]+=1
        hits+=(prediction==y)
        brier+=sum((p[i]-float(i==y))**2 for i in range(7))
        nll-=log(max(p[y],1e-12))
    n=len(labels)
    return {"rows":n,"brier":brier/n,"nll":nll/n,
            "top1_hits":hits,"top1_accuracy":hits/n,
            "predicted_class_counts":{str(i):predicted[i] for i in range(7)},
            "actual_class_counts":{str(i):observed[i] for i in range(7)},
            "no_trading_profit_claim":True}


def frequency_prior(rows):
    counts = Counter(r["target_class"] for r in rows)
    n = len(rows)
    if n < 16 or any(type(k) is not int or not 0 <= k < 7 for k in counts):
        raise ContractError("GPU_ABLATION_PRIOR_INPUT")
    return [(counts[i]+ALPHA)/(n+7*ALPHA) for i in range(7)]


def choose_validation(scores):
    family = ("train_frequency_prior", "frozen_parent") + ARMS
    if set(scores) != set(family):
        raise ContractError("GPU_ABLATION_SELECTION_FAMILY_CHANGED")
    for name in family:
        if not all(type(scores[name].get(field)) in (int,float) and
                   isfinite(scores[name][field]) for field in ("brier","nll")):
            raise ContractError("GPU_ABLATION_BAD_VALIDATION_SCORE")
    return min(family,key=lambda k:(scores[k]["brier"],family.index(k)))


def promotion_screen(selected, test_scores, cohort_value):
    baseline=test_scores["train_frequency_prior"]
    chosen=test_scores[selected]
    independent=independent_count(cohort_value["test"])
    return {"schema":"heptatrader.laya-price-gpu-ablation-screen.v1",
            "selected_on_validation":selected,
            "test_independent_nonoverlap_5min":independent,
            "minimum_independent_required":MIN_INDEPENDENT_TEST_HORIZONS,
            "beats_static_prior_brier":chosen["brier"] < baseline["brier"],
            "beats_static_prior_nll":chosen["nll"] < baseline["nll"],
            "research_price_signal_qualified":False,
            "reason":"INSUFFICIENT_INDEPENDENT_HORIZONS_FOR_PRODUCTION",
            "no_model_selection_or_trading_authority":True}
