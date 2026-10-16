"""Temporal seven-bin price calibration benchmark; no authority or refit of Laya.

Uses the same hash-checked, causally observed, closed historical population
as action_quality_research. The model-selection day and final evaluation
day are disjoint; this is NOT formal prospective statistical qualification.
"""
from __future__ import annotations

from collections import Counter
import math

from .integrity import ContractError, digest
from .action_quality_research import chronological_days, probabilities

SCHEMA = "heptatrader.price-quality-research.v1"
SMOOTHING_GRID = (0.01, 0.1, 0.5, 1.0, 4.0, 16.0, 64.0)
CLASSES = 7
BLEND_GRID = (0.0, 0.01, 0.05, 0.1, 0.2, 0.5, 1.0)


def prior(training, alpha):
    if not training or alpha not in SMOOTHING_GRID:
        raise ContractError("PRICE_PRIOR_TRAINING_OR_SMOOTHING")
    counts = Counter()
    for row in training:
        target = row.get("target_class")
        if type(target) is not int or not 0 <= target < CLASSES:
            raise ContractError("PRICE_PRIOR_TARGET")
        counts[target] += 1
    d = len(training) + CLASSES * alpha
    weights = [(counts[i] + alpha) / d for i in range(CLASSES)]
    probabilities(weights, CLASSES)
    return {"schema": "heptatrader.historical-price-frequency-prior.v1",
            "smoothing": alpha, "training_rows": len(training),
            "training_class_counts": {str(i): counts[i] for i in range(CLASSES)},
            "weights": weights, "trade_authority": False, "model_promotion": False}


def score(data, mode, *, constant=None):
    if not data:
        raise ContractError("PRICE_SCORING_EMPTY_DATASET")
    if mode not in ("parent", "candidate", "constant"):
        raise ContractError("PRICE_SCORING_MODE")
    if mode == "constant":
        weights = probabilities(constant, CLASSES)
    total_brier = total_nll = 0.0
    hits = 0
    chosen_counts = Counter()
    targets = Counter()
    for row in data:
        target = row.get("target_class")
        if type(target) is not int or not 0 <= target < CLASSES:
            raise ContractError("PRICE_SCORING_TARGET")
        if mode == "parent":
            probs = probabilities(row["parent_probabilities"], CLASSES)
        elif mode == "candidate":
            probs = probabilities(row["candidate_probabilities"], CLASSES)
        else:
            probs = weights
        prediction = max(range(CLASSES), key=lambda i: probs[i])  # first index wins a tie
        hits += prediction == target
        chosen_counts[prediction] += 1
        targets[target] += 1
        total_brier += sum((p - (i == target)) ** 2 for i, p in enumerate(probs))
        total_nll += -math.log(max(probs[target], 1e-12))
    return {"rows": len(data), "brier": total_brier / len(data),
            "negative_log_likelihood": total_nll / len(data),
            "top1_hits": hits, "top1_accuracy": hits / len(data),
            "predicted_class_counts": {str(i):chosen_counts[i] for i in range(CLASSES)},
            "observed_class_counts": {str(i):targets[i] for i in range(CLASSES)},
            "top1_tie_policy": "LOWEST_CLASS_INDEX",
            "economic_efficacy_claim": False}


def score_blend(data, prior_weights, source, model_weight):
    """Mix a frozen training-only prior with original model probabilities.

    Weights are selected using validation only, and are never interpreted
    as a production action or a model-promotion qualification.
    """
    if source not in ("parent", "candidate") or model_weight not in BLEND_GRID:
        raise ContractError("PRICE_BLEND_UNDECLARED_FAMILY")
    base = probabilities(prior_weights, CLASSES)
    key = "parent_probabilities" if source == "parent" else "candidate_probabilities"
    mixed = []
    for row in data:
        original = probabilities(row[key], CLASSES)
        q = [(1 - model_weight) * base[i] + model_weight * original[i]
             for i in range(CLASSES)]
        mixed.append({"target_class":row["target_class"],"candidate_probabilities":q})
    return score(mixed, "candidate")


def experiment(rows, provenance):
    training, validation, test = chronological_days(rows)
    candidates = {alpha: prior(training, alpha) for alpha in SMOOTHING_GRID}
    validations = {str(alpha): score(validation, "constant", constant=model["weights"])
                   for alpha, model in candidates.items()}
    # Deterministic tie break selects least smoothing; test data NEVER selects alpha.
    chosen_alpha = min(SMOOTHING_GRID, key=lambda a: (
        validations[str(a)]["brier"], SMOOTHING_GRID.index(a)))
    winner = candidates[chosen_alpha]
    uniform = [1 / CLASSES] * CLASSES
    center = [float(i == 3) for i in range(CLASSES)]
    test_scores = {"parent": score(test, "parent"),
                   "candidate": score(test, "candidate"),
                   "uniform": score(test, "constant", constant=uniform),
                   "middle_class_onehot": score(test, "constant", constant=center),
                   "train_frequency_prior": score(test, "constant", constant=winner["weights"])}
    validation_scores = {"parent":score(validation, "parent"),
                         "candidate":score(validation, "candidate"),
                         "selected_train_frequency_prior":validations[str(chosen_alpha)]}
    blends = {}
    for kind in ("parent", "candidate"):
        options = {str(w): score_blend(validation, winner["weights"], kind, w)
                   for w in BLEND_GRID}
        chosen_weight = min(BLEND_GRID, key=lambda w: (
            options[str(w)]["brier"], BLEND_GRID.index(w)))
        blends[kind] = {"validation_scores": options,
                        "validation_selected_model_weight": chosen_weight,
                        "test_score": score_blend(test, winner["weights"], kind, chosen_weight)}
    report = {"schema":SCHEMA,
              "population":"CLOSED_ORIGINAL_SHADOW_OUTCOMES_NOT_RANDOMIZED",
              "source":provenance,
              "split_days":{"training":sorted({r["utc_day"] for r in training}),
                            "validation":sorted({r["utc_day"] for r in validation}),
                            "test":sorted({r["utc_day"] for r in test})},
              "split_counts":{"training":len(training),"validation":len(validation),"test":len(test)},
              "alpha_grid_fixed_before_test":list(SMOOTHING_GRID),
              "validation_scores_by_alpha":validations,
              "selection":"MIN_VALIDATION_BRIER_TIE_BREAK_GRID_ORDER",
              "selected_smoothing":chosen_alpha, "selected_prior":winner,
              "validation_scores":validation_scores,"test_scores":test_scores,
              "blend_weight_grid_fixed_before_test": list(BLEND_GRID),
              "validation_selected_blends": blends,
              "source_bin_edges_basis_points":[-10.,-3.,-1.,1.,3.,10.],
              "claims":{"retrospective_only":True,"no_model_refit":True,
                        "no_order_authority":True,"no_model_promotion":True,
                        "future_coverage_proven":False,"economic_alpha_proven":False}}
    report["report_id"] = digest(report)
    return report
