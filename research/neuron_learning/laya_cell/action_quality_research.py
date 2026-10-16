"""Read-only, retrospective ACT/ESCALATE quality analysis.

No order authority, policy gradients, model checkpoint edits, retroactive
predictions, or claim of prospective statistical qualification. Input rows
come only from frozen, closed, genuinely observed shadow slots.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .integrity import ContractError, digest, read_json, sha_file

SCHEMA = "heptatrader.action-quality-research.v1"
MIN_ROWS_PER_DAY = 10
ACTION_COST_WRONG = 3.0
ACTION_COST_ESCALATE = 0.5
MIN_ACT_CORRECT_PROB = 1.0 - ACTION_COST_ESCALATE / ACTION_COST_WRONG
PRIOR_STRENGTH = 16.0
MAX_PLANS = 512
MAX_ROWS = 100000


def probabilities(probs, count):
    if not isinstance(probs, list) or len(probs) != count:
        raise ContractError("CALIBRATION_PROBABILITY_SHAPE")
    if any(type(x) not in (float, int) or not math.isfinite(x) or not 0 <= x <= 1 for x in probs):
        raise ContractError("CALIBRATION_PROBABILITY_RANGE")
    if abs(sum(probs) - 1) > 1e-5:
        raise ContractError("CALIBRATION_PROBABILITY_NORMALIZATION")
    return probs


def brier_for_correctness(p_correct, was_correct):
    if (type(p_correct) not in (float, int) or not math.isfinite(p_correct)
            or not 0 <= p_correct <= 1 or type(was_correct) is not bool):
        raise ContractError("CALIBRATION_BRIER_INPUT")
    return 2.0 * (p_correct - float(was_correct)) ** 2


def collect_closed(root):
    root = Path(root)
    plans = sorted(root.glob("*/plan.json"))
    if len(plans) > MAX_PLANS:
        raise ContractError("CALIBRATION_PLAN_CAPACITY")
    rows = []
    seen = set()
    evidence = hashlib.sha256()
    source_count = 0
    for file in plans:
        qualifier = file.parent / "qualification.json"
        if not qualifier.exists():
            continue  # A still-PENDING plan is NOT historical calibration data.
        plan = read_json(file)
        q = read_json(qualifier)
        if (plan.get("plan_id") != digest({k: v for k, v in plan.items() if k != "plan_id"})
                or q.get("plan_id") != plan["plan_id"]
                or q.get("candidate_id") != plan.get("candidate_id")
                or q.get("state") not in ("REJECTED", "QUALIFIED_AWAITING_CANONICAL_OWNER")):
            raise ContractError("CALIBRATION_PLAN_OR_QUALIFICATION_IDENTITY")
        for f in (file, qualifier):
            evidence.update(str(f).encode() + bytes.fromhex(sha_file(f)))
            source_count += 1
        for f in sorted(file.parent.glob("outcome-*.json")):
            obs = read_json(f)
            if (obs.get("plan_id") != plan["plan_id"] or
                    obs.get("candidate_id") != plan["candidate_id"] or
                    type(obs.get("slot")) is not int):
                raise ContractError("CALIBRATION_OUTCOME_IDENTITY")
            if obs.get("status") != "OBSERVED":
                continue
            if len(rows) >= MAX_ROWS:
                raise ContractError("CALIBRATION_OBSERVATION_CAPACITY")
            slot = obs["slot"]
            pred = file.parent / ("prediction-%03d.json" % slot)
            if not pred.is_file() or obs.get("prediction_file_sha256") != sha_file(pred):
                raise ContractError("CALIBRATION_PREDICTION_MISSING_OR_CHANGED")
            p = read_json(pred)
            if (p.get("plan_id") != plan["plan_id"] or p.get("candidate_id") != plan["candidate_id"] or
                    p.get("slot") != slot or p.get("anchor_ms") != obs.get("anchor_ms")):
                raise ContractError("CALIBRATION_PREDICTION_IDENTITY")
            anchor = p["anchor_ms"]
            if (type(anchor) is not int or type(p.get("created_at_ms")) is not int or
                    type(p.get("trained_until_ms")) is not int or
                    p["trained_until_ms"] > anchor or
                    p["created_at_ms"] >= anchor + plan["rule"]["horizon_ms"] or
                    p["created_at_ms"] >= obs.get("outcome_ms", -1)):
                raise ContractError("CALIBRATION_NONCAUSAL_PREDICTION")
            if anchor in seen:
                raise ContractError("CALIBRATION_OVERLAPPING_OBSERVED_ANCHORS")
            seen.add(anchor)
            parent = probabilities(p["parent_probs"], 7)
            candidate = probabilities(p["candidate_probs"], 7)
            action = probabilities(p["candidate_act_escalate_probs"], 2)
            target = obs.get("target_class")
            if type(target) is not int or not 0 <= target < 7:
                raise ContractError("CALIBRATION_TARGET")
            parent_correct = max(range(7), key=lambda i: parent[i]) == target
            raw_brier = brier_for_correctness(action[0], parent_correct)
            if (type(obs.get("action_head_brier")) not in (float, int) or
                    abs(raw_brier - obs["action_head_brier"]) > 1e-5):
                raise ContractError("CALIBRATION_REPORTED_SCORE_CHANGED")
            evidence.update(str(f).encode() + bytes.fromhex(sha_file(f)))
            evidence.update(str(pred).encode() + bytes.fromhex(sha_file(pred)))
            source_count += 2
            rows.append({"anchor_ms": anchor,
                         "utc_day": datetime.fromtimestamp(anchor / 1000, tz=timezone.utc).date().isoformat(),
                         "parent_confidence": max(parent),
                         "parent_probabilities": list(parent),
                         "candidate_probabilities": list(candidate),
                         "target_class": target,
                         "parent_correct": parent_correct,
                         "raw_action_correct_probability": action[0],
                         "raw_action_brier": raw_brier,
                         "plan_id": plan["plan_id"]})
    if not rows:
        raise ContractError("CALIBRATION_NO_CLOSED_OBSERVATIONS")
    return sorted(rows, key=lambda r: r["anchor_ms"]), {
        "source_files_sha256": evidence.hexdigest(), "source_files_count": source_count,
        "observations": len(rows), "closed_plans": sum((p.parent / "qualification.json").exists() for p in plans),
        "pending_plans_excluded": sum(not (p.parent / "qualification.json").exists() for p in plans)}


def chronological_days(rows):
    days = sorted({r["utc_day"] for r in rows})
    if len(days) < 3:
        raise ContractError("CALIBRATION_THREE_DAYS_REQUIRED")
    training = [r for r in rows if r["utc_day"] == days[0]]
    validation = [r for r in rows if r["utc_day"] == days[1]]
    test = [r for r in rows if r["utc_day"] >= days[2]]
    if min(len(training), len(validation), len(test)) < MIN_ROWS_PER_DAY:
        raise ContractError("CALIBRATION_INSUFFICIENT_TEMPORAL_HOLDOUT")
    for block in (training, validation, test):
        if len({r["anchor_ms"] for r in block}) != len(block):
            raise ContractError("CALIBRATION_DUPLICATE_SPLIT_ANCHOR")
    return training, validation, test


def fit(training, bins):
    if bins not in (1, 2, 4) or not training:
        raise ContractError("CALIBRATION_MODEL_FAMILY")
    wins = sum(r["parent_correct"] for r in training)
    global_correct = (wins + 1) / (len(training) + 2)
    ordered = sorted(r["parent_confidence"] for r in training)
    thresholds = sorted(set(ordered[(len(ordered) * i) // bins] for i in range(1, bins)))
    rates = []
    for b in range(len(thresholds) + 1):
        rows = [r for r in training if bisect_right(thresholds, r["parent_confidence"]) == b]
        successes = sum(r["parent_correct"] for r in rows)
        rates.append((successes + PRIOR_STRENGTH * global_correct) / (len(rows) + PRIOR_STRENGTH))
    model = {"schema": "heptatrader.action-correctness-prior.v1", "bins_requested": bins,
             "bin_thresholds_training_only": thresholds, "correctness_rates": rates,
             "training_correct": wins, "training_rows": len(training),
             "laplace_global_correctness": global_correct,
             "trade_authority": False, "actor_update": False, "model_promotion": False}
    model["model_id"] = digest(model)
    return model


def predict_correctness(model, row):
    if model is None:
        return 0.0  # Conservative always-ESCALATE benchmark.
    if model.get("model_id") != digest({k: v for k, v in model.items() if k != "model_id"}):
        raise ContractError("CALIBRATION_MODEL_CHANGED")
    return model["correctness_rates"][bisect_right(model["bin_thresholds_training_only"],
                                                     row["parent_confidence"])]


def score(model, rows, *, raw=False):
    if not rows:
        raise ContractError("CALIBRATION_EMPTY_SCORING_POPULATION")
    probabilities_correct = ([r["raw_action_correct_probability"] for r in rows] if raw else
                             [predict_correctness(model, r) for r in rows])
    briers = [brier_for_correctness(p, r["parent_correct"]) for p, r in zip(probabilities_correct, rows)]
    would_act = [p >= MIN_ACT_CORRECT_PROB for p in probabilities_correct]
    # Internal ACT/ESCALATE decision-support proxy, not execution PnL.
    proxy_cost = [0.0 if act and r["parent_correct"] else
                  ACTION_COST_WRONG if act else ACTION_COST_ESCALATE
                  for act, r in zip(would_act, rows)]
    return {"rows": len(rows), "correct": sum(r["parent_correct"] for r in rows),
            "brier": sum(briers) / len(rows),
            "act_suggestions": sum(would_act), "escalate_suggestions": len(rows) - sum(would_act),
            "cost_proxy_mean": sum(proxy_cost) / len(rows),
            "no_order_or_economic_return_claim": True}


def experiment(rows, provenance):
    training, validation, test = chronological_days(rows)
    candidates = {"always_escalate": None}
    for bins in (1, 2, 4):
        candidates["training_only_bins_%d" % bins] = fit(training, bins)
    validation_scores = {name: score(m, validation) for name, m in candidates.items()}
    # Predeclare tie-break to the simpler model. No choice depends on the test outcomes.
    rank = ["always_escalate", "training_only_bins_1", "training_only_bins_2", "training_only_bins_4"]
    winner = min(rank, key=lambda name: (validation_scores[name]["brier"], rank.index(name)))
    selected = candidates[winner]
    test_scores = {name: score(model, test) for name, model in candidates.items()}
    test_scores["original_saturated_action_head"] = score(None, test, raw=True)
    report = {"schema": SCHEMA, "population": "CLOSED_HISTORICAL_OBSERVATIONAL_NOT_RANDOMIZED",
              "source": provenance, "days": {"training": training[0]["utc_day"],
                  "validation": validation[0]["utc_day"],
                  "test": sorted({r["utc_day"] for r in test})},
              "counts": {"train": len(training), "validation": len(validation), "test": len(test)},
              "validation_scores": validation_scores,
              "selection_rule": "LOWEST_VALIDATION_BRIER_TIE_BREAK_SIMPLE",
              "validation_selected": winner, "selected_model": selected,
              "test_scores_after_selection": test_scores,
              "claims": {"retrospective_only": True, "original_prediction_files_unchanged": True,
                         "automatic_trading": False, "order_authority": False,
                         "statistical_promotion": False, "future_coverage_proven": False,
                         "economic_alpha_proven": False}}
    report["report_id"] = digest(report)
    return report
