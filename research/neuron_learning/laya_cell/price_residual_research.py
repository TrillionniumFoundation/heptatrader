"""Frozen-base price residual checks with validation-only selection.

Research-only transforms on 7-bin probabilities; no optimizer, order, or
independent trial registration. Both model families fit only to the first UTC
date; the next date selects. The last date is held out for reporting.
"""
from __future__ import annotations
from collections import Counter
import math
from .action_quality_research import chronological_days, probabilities
from .price_quality_research import prior, score, score_blend, CLASSES
from .integrity import ContractError, digest

SCHEMA = "heptatrader.price-residual-research.v1"
COUNTS = (2.0, 8.0, 32.0, 128.0)
POWERS = (0.25, 0.5, 1.0, 1.5)
SOURCES = ("parent", "candidate")
EPS = 1e-8
MAX_RATIO = 1000.0

def source_vector(row, source):
    if source not in SOURCES:
        raise ContractError("PRICE_RESIDUAL_SOURCE_NOT_REGISTERED")
    return probabilities(row[source + "_probabilities"], CLASSES)

def fit(training, source, family, parameter, global_weights):
    if not training or source not in SOURCES:
        raise ContractError("PRICE_RESIDUAL_TRAINING_OR_SOURCE")
    global_weights = probabilities(global_weights, CLASSES)
    if family == "conditional":
        if parameter not in COUNTS:
            raise ContractError("PRICE_RESIDUAL_CONDITIONAL_PSEUDOCOUNT")
        matrix = [[0] * CLASSES for _ in range(CLASSES)]
        for row in training:
            y = row.get("target_class")
            if type(y) is not int or not 0 <= y < CLASSES:
                raise ContractError("PRICE_RESIDUAL_TARGET")
            v = source_vector(row, source)
            k = max(range(CLASSES), key=lambda i: v[i])
            matrix[k][y] += 1
        conditionals = [
            [(matrix[k][i] + parameter*global_weights[i])/(sum(matrix[k])+parameter)
             for i in range(CLASSES)] for k in range(CLASSES)
        ]
        for a in conditionals: probabilities(a, CLASSES)
        model = {"schema": "heptatrader.price-conditional-prior.v1",
                 "source": source, "family": family, "parameter": parameter,
                 "counts": matrix, "conditionals": conditionals,
                 "training_rows": len(training), "global_weights": list(global_weights)}
    elif family == "prior_ratio":
        if parameter not in POWERS:
            raise ContractError("PRICE_RESIDUAL_POWER")
        mean = [0.0]*CLASSES
        for row in training:
            y = row.get("target_class")
            if type(y) is not int or not 0 <= y < CLASSES:
                raise ContractError("PRICE_RESIDUAL_TARGET")
            for i,v in enumerate(source_vector(row, source)):
                mean[i] += v/len(training)
        mean = [max(v,EPS) for v in mean]
        ratios = [max(1/MAX_RATIO, min(MAX_RATIO, global_weights[i]/mean[i]))
                  for i in range(CLASSES)]
        model = {"schema": "heptatrader.price-prior-ratio.v1",
                 "source": source, "family": family, "parameter": parameter,
                 "training_mean_prediction": mean, "ratios": ratios,
                 "training_rows": len(training), "global_weights": list(global_weights)}
    else:
        raise ContractError("PRICE_RESIDUAL_UNKNOWN_FAMILY")
    model.update({"trade_authority": False, "model_promotion": False,
                  "refitted_on_validation_or_test": False, "original_model_modified": False})
    model["model_id"] = digest(model)
    return model

def forecast(model, probs):
    if (not isinstance(model, dict) or model.get("model_id") !=
            digest({k:v for k,v in model.items() if k!="model_id"}) or
            model.get("source") not in SOURCES or model.get("trade_authority") is not False):
        raise ContractError("PRICE_RESIDUAL_MODEL_INVALID")
    probs = probabilities(probs, CLASSES)
    if model["family"] == "conditional":
        k = max(range(CLASSES), key=lambda i: probs[i])
        q = model["conditionals"][k]
    elif model["family"] == "prior_ratio":
        weight = model["parameter"]
        modified = [max(x,EPS) * model["ratios"][i]**weight
                    for i,x in enumerate(probs)]
        den = sum(modified)
        if not math.isfinite(den) or den<=0:
            raise ContractError("PRICE_RESIDUAL_NORMALIZATION_FAILED")
        q = [v/den for v in modified]
    else:
        raise ContractError("PRICE_RESIDUAL_FAMILY_CHANGED")
    return list(probabilities(q, CLASSES))

def score_model(model, data):
    if not data: raise ContractError("PRICE_RESIDUAL_EMPTY_SCORE")
    vals = []
    for row in data:
        vals.append({"target_class":row["target_class"],
                     "candidate_probabilities":forecast(model,source_vector(row,model["source"]))})
    return score(vals,"candidate")

def experiment(rows, provenance):
    train, validation, test = chronological_days(rows)
    # Freeze an intentionally small grid before inspecting validation or test.
    global_prior = prior(train,.5)
    models = {"train_only_frequency": None}
    for source in SOURCES:
        for parameter in COUNTS:
            name = source + "_conditional_" + str(parameter)
            models[name] = fit(train,source,"conditional",parameter,global_prior["weights"])
        for parameter in POWERS:
            name = source + "_prior_ratio_" + str(parameter)
            models[name] = fit(train,source,"prior_ratio",parameter,global_prior["weights"])
    def rate(model, data):
        if model is None:
            return score(data,"constant",constant=global_prior["weights"])
        return score_model(model,data)
    results_val = {name:rate(m,validation) for name,m in models.items()}
    # Deterministic original grid order resolves any ties to simpler baseline.
    names = list(models)
    winner = min(names,key=lambda name:(results_val[name]["brier"],names.index(name)))
    tests = {
        "raw_candidate":score(test,"candidate"),
        "raw_parent":score(test,"parent"),
        "train_only_frequency":rate(None,test),
        "validation_selected":rate(models[winner],test),
    }
    report = {"schema":SCHEMA,"source":provenance,
        "counts":{"train":len(train),"validation":len(validation),"test":len(test)},
        "days":{"train":sorted({r["utc_day"] for r in train}),
                "validation":sorted({r["utc_day"] for r in validation}),
                "test":sorted({r["utc_day"] for r in test})},
        "model_grid_fixed_before_validation_and_test":{
             "sources":list(SOURCES),"conditional_pseudocounts":list(COUNTS),
             "prior_ratio_powers":list(POWERS),"training_global_smoothing":.5},
        "training_prior":global_prior,
        "selection_rule":"MIN_VALIDATION_BRIER_DETERMINISTIC_GRID_ORDER",
        "validation_scores":results_val,
        "validation_selected":winner,
        "selected_model":models[winner],
        "test_scores":tests,
        "claims":{"retrospective_only":True,"no_future_label_fit":True,
            "no_trading_authority":True,"no_model_promotion":True,
            "no_prospective_success_claim":True,"economic_edge_proven":False}}
    report["report_id"]=digest(report)
    return report
