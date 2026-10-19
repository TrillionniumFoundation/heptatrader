"""Re-score sealed action-weight ablations without loading Torch or training.

All scores remain retrospective diagnostics. This module has no promotion,
scheduler, broker, or runtime mutation interface.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics
import struct


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, code):
    if not condition:
        raise ValueError(code)


def distribution(values):
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def score_actions(logits, labels, temperature=1.0):
    """ACT is index zero; stable logit NLL must retain huge incorrect margins."""
    require(len(logits) == len(labels) and len(labels) > 0, "ACTION_ROW_COUNT")
    require(math.isfinite(temperature) and temperature > 0, "ACTION_TEMPERATURE")
    require(all(type(y) is bool for y in labels), "ACTION_LABEL_TYPE")
    nll, brier, probabilities, margins = [], [], [], []
    for values, is_act in zip(logits, labels):
        require(len(values) == 2 and all(math.isfinite(v) for v in values), "ACTION_LOGITS")
        margin = (values[0] - values[1]) / temperature
        require(math.isfinite(margin), "ACTION_MARGIN")
        x = -margin if is_act else margin
        loss = max(x, 0.0) + math.log1p(math.exp(-abs(x)))
        p = 1.0 / (1.0 + math.exp(-margin)) if margin >= 0 else math.exp(margin) / (1.0 + math.exp(margin))
        margins.append(margin); probabilities.append(p); nll.append(loss)
        brier.append(2.0 * (p - int(is_act)) ** 2)
    def confusion(threshold):
        predicted = [p >= threshold for p in probabilities]
        tp = sum(p and y for p, y in zip(predicted, labels))
        fp = sum(p and not y for p, y in zip(predicted, labels))
        tn = sum(not p and not y for p, y in zip(predicted, labels))
        fn = sum(not p and y for p, y in zip(predicted, labels))
        return {"predicted_act": sum(predicted), "tp": tp, "fp": fp, "tn": tn, "fn": fn}
    classes = {}
    for flag, name in ((True, "ACT"), (False, "ESCALATE")):
        indices = [i for i, y in enumerate(labels) if y is flag]
        classes[name] = {"rows": len(indices),
                         "nll": statistics.mean(nll[i] for i in indices) if indices else None,
                         "brier_sum_two_classes": statistics.mean(brier[i] for i in indices) if indices else None}
    return {"rows": len(labels), "nll": statistics.mean(nll),
            "brier_sum_two_classes": statistics.mean(brier),
            "temperature": temperature, "act_probability": distribution(probabilities),
            "act_minus_escalate_margin": distribution(margins),
            "thresholds": {"0.5": confusion(0.5), "5/6": confusion(5.0 / 6.0)},
            "true_classes": classes}


def score_prices(probabilities, labels):
    require(len(probabilities) == len(labels) and len(labels) > 0, "PRICE_ROW_COUNT")
    require(all(type(y) is int and 0 <= y < 7 for y in labels), "PRICE_LABEL_TYPE")
    nll, brier, predicted = [], [], []
    confusion = [[0] * 7 for _ in range(7)]
    for values, y in zip(probabilities, labels):
        require(len(values) == 7 and all(math.isfinite(p) and 0 <= p <= 1 for p in values)
                and math.isclose(sum(values), 1.0, abs_tol=1e-5), "PRICE_PROBABILITIES")
        pred = max(range(7), key=values.__getitem__)
        predicted.append(pred); confusion[y][pred] += 1
        nll.append(-math.log(max(values[y], 1e-30)))
        brier.append(sum((p - int(i == y)) ** 2 for i, p in enumerate(values)))
    true_counts, pred_counts = Counter(labels), Counter(predicted)
    by_class = []
    for c in range(7):
        indices = [i for i, y in enumerate(labels) if y == c]
        by_class.append({"class": c, "rows": len(indices),
                         "predicted_rows": pred_counts[c],
                         "nll": statistics.mean(nll[i] for i in indices) if indices else None})
    return {"rows": len(labels), "nll": statistics.mean(nll),
            "brier_sum_seven_classes": statistics.mean(brier),
            "accuracy": sum(p == y for p, y in zip(predicted, labels)) / len(labels),
            "true_class_counts": [true_counts[i] for i in range(7)],
            "predicted_class_counts": [pred_counts[i] for i in range(7)],
            "confusion_true_rows_predicted_columns": confusion,
            "true_classes": by_class, "probability_nll_floor": 1e-30}


def aligned_holdout(source, result, predictions):
    rows = sorted((r for r in source["rows"]
                   if result["train_cutoff_ms"] <= r["anchor_ms"] < result["holdout_end_ms"]),
                  key=lambda r: (r["anchor_ms"], r["row_id"]))
    ids = [r["row_id"] for r in rows]
    require(len(ids) == result["heldout_rows"] and len(set(ids)) == len(ids), "HOLDOUT_IDENTITIES")
    require(predictions["row_ids"] == ids, "HOLDOUT_ROW_ORDER")
    require(len(predictions["prices"]) == len(ids) == len(predictions["actions"]), "HOLDOUT_ARRAY_COUNT")
    return rows


def read_tensor_payloads(path):
    raw = Path(path).read_bytes()
    require(len(raw) >= 8, "TENSOR_HEADER")
    length = struct.unpack("<Q", raw[:8])[0]
    require(0 < length <= len(raw) - 8, "TENSOR_HEADER_LENGTH")
    header, payload = json.loads(raw[8:8 + length]), raw[8 + length:]
    result = {}
    for name, info in header.items():
        if name == "__metadata__":
            continue
        dtype, shape = info["dtype"], info["shape"]
        require(dtype in ("F32", "F64", "F16", "BF16"), "TENSOR_DTYPE")
        require(all(type(v) is int and v >= 0 for v in shape), "TENSOR_SHAPE")
        count = math.prod(shape); sizes = {"F32": 4, "F64": 8, "F16": 2, "BF16": 2}
        begin, end = info["data_offsets"]
        require(type(begin) is int and type(end) is int and 0 <= begin <= end <= len(payload)
                and end - begin == count * sizes[dtype], "TENSOR_OFFSETS")
        data = payload[begin:end]
        if dtype == "BF16":
            values = [struct.unpack("<f", struct.pack("<I", v[0] << 16))[0]
                      for v in struct.iter_unpack("<H", data)]
        else:
            values = list(struct.unpack("<" + str(count) + {"F32": "f", "F64": "d", "F16": "e"}[dtype], data))
        require(all(math.isfinite(v) for v in values), "TENSOR_FINITE")
        result[name] = {"dtype": dtype, "shape": shape, "bytes": data, "values": values}
    require(bool(result), "NO_TENSORS")
    return result


def compare_tensors(left, right, marker):
    names = sorted(n for n in left if marker in n)
    require(names and names == sorted(n for n in right if marker in n), "TENSOR_NAMES")
    differences, exact = [], True
    for name in names:
        a, b = left[name], right[name]
        require((a["dtype"], a["shape"]) == (b["dtype"], b["shape"]), "TENSOR_LAYOUT")
        exact = exact and a["bytes"] == b["bytes"]
        differences.extend(x - y for x, y in zip(a["values"], b["values"]))
    return {"tensor_names": names, "exact_payload_equality": exact,
            "changed_coordinates": sum(v != 0 for v in differences),
            "max_absolute_difference": max(abs(v) for v in differences),
            "factor_l2_difference": math.sqrt(math.fsum(v * v for v in differences))}


def summarize(result_directory, source_path):
    out, source_path = Path(result_directory), Path(source_path)
    result = json.loads((out / "result.json").read_text())
    require(result["status"] == "COMPLETED", "RUN_NOT_COMPLETED")
    require(sha256(source_path) == result["source_sha256"], "SOURCE_SHA")
    require(sha256(out / "frozen-train-profile.json") == result["profile_sha256"], "PROFILE_SHA")
    source = json.loads(source_path.read_text())
    profile = json.loads((out / "frozen-train-profile.json").read_text())
    temperature = profile["temperature_training_only"]
    all_ids = [r["row_id"] for r in source["rows"]]
    require(len(all_ids) == len(set(all_ids)), "SOURCE_DUPLICATE_ROWS")
    train = [r for r in source["rows"] if r["anchor_ms"] < result["train_cutoff_ms"]]
    require(len(train) == result["training_pool_rows"], "TRAIN_COUNT")
    require(all(r["future_outcome_available_ms"] <= result["train_cutoff_ms"] for r in train), "TRAIN_OUTCOME_LEAK")
    counts = [sum(r["target_class"] == i for r in train) for i in range(7)]
    price_prior = [(v + 0.5) / (len(train) + 3.5) for v in counts]
    action_prior = (sum(r["parent_correct"] for r in train) + 0.5) / (len(train) + 1)
    manifest = {str(source_path): sha256(source_path)}
    for name in ("result.json", "frozen-train-profile.json", "sandbox.json"):
        manifest[str(out / name)] = sha256(out / name)
    arms, tensors, prediction_arrays, hold = [], {}, {}, None
    for arm in result["arms"]:
        name = arm["name"]
        require(name in ("price_only", "current_001", "gradient_equalized") and name not in tensors, "ARM_NAME")
        predictions = json.loads((out / (name + "-holdout.json")).read_text())
        rows = aligned_holdout(source, result, predictions); hold = rows
        require(json.loads((out / (name + "-result.json")).read_text()) == arm, "ARM_RESULT_IDENTITY")
        adapter = out / (name + ".safetensors")
        require(sha256(adapter) == arm["adapter_sha256"], "ADAPTER_SHA")
        tensors[name] = read_tensor_payloads(adapter); prediction_arrays[name] = predictions
        for suffix in (".safetensors", "-holdout.json", "-result.json"):
            manifest[str(out / (name + suffix))] = sha256(out / (name + suffix))
        truth = [r["target_class"] for r in rows]; action_truth = [r["parent_correct"] for r in rows]
        price = score_prices(predictions["prices"], truth)
        raw = score_actions(predictions["actions"], action_truth)
        scaled = score_actions(predictions["actions"], action_truth, temperature)
        recorded = arm["holdout"]
        for actual, expected in ((price["nll"], recorded["price_nll"]),
                                 (price["brier_sum_seven_classes"], recorded["price_brier"]),
                                 (raw["nll"], recorded["raw_action_nll"]),
                                 (raw["brier_sum_two_classes"], recorded["raw_action_brier"]),
                                 (scaled["nll"], recorded["train_temperature_action_nll"]),
                                 (scaled["brier_sum_two_classes"], recorded["train_temperature_action_brier"])):
            require(math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10), "SCORE_REPRODUCTION")
        steps = arm["steps"]
        require(len(steps) == arm["attempted_steps"] == result["attempted_steps_per_arm"], "STEP_COUNT")
        require(sum(s["applied"] for s in steps) == arm["applied_steps"], "APPLIED_STEP_COUNT")
        require(sum(not s["applied"] for s in steps) == arm["rollback_steps"], "ROLLBACK_STEP_COUNT")
        training = {"attempted": len(steps), "applied": arm["applied_steps"], "rollback": arm["rollback_steps"],
                    "clipped_steps": sum(s["diagnostics"]["clip_factor"] < 1 for s in steps),
                    "max_gradient_norm_before_clip": max(s["gradient_norm_before_clip"] for s in steps),
                    "max_shared_trainable_tensors": max(s["diagnostics"]["graph_shared_tensors"] for s in steps),
                    "max_overlapping_gradient_coordinates": max(s["diagnostics"]["overlap_numel"] for s in steps),
                    "all_gradients_finite": all(s["diagnostics"]["finite"] for s in steps),
                    "parameter_changes_from_initialization": arm["parameter_changes"]}
        arms.append({"name": name, "weight": arm["weight"], "training": training, "price": price,
                     "raw_action": raw, "train_temperature_action": scaled})
    require(set(tensors) == {"price_only", "current_001", "gradient_equalized"}, "THREE_ARMS_REQUIRED")
    truth = [r["target_class"] for r in hold]; action_truth = [r["parent_correct"] for r in hold]
    baselines = {"price": score_prices([price_prior] * len(hold), truth),
                 "action": score_actions([[math.log(action_prior), math.log1p(-action_prior)]] * len(hold), action_truth),
                 "price_training_counts": counts, "price_probabilities": price_prior,
                 "action_training_act": sum(r["parent_correct"] for r in train),
                 "action_probability": action_prior, "smoothing_per_class": 0.5}
    parent_price = score_prices([r["frozen_parent_probs"] for r in hold], truth)
    require(math.isclose(parent_price["nll"], result["parent_holdout"]["price_nll"], abs_tol=1e-10), "PARENT_PRICE_REPRODUCTION")
    comparisons = {name: compare_tensors(tensors["price_only"], tensors[name], ".scorer.")
                   for name in tensors}
    action_comparison = compare_tensors(tensors["current_001"], tensors["gradient_equalized"], ".act_head.")
    prediction_equality = {kind: prediction_arrays["current_001"][kind] == prediction_arrays["gradient_equalized"][kind]
                           for kind in ("prices", "actions", "row_ids")}
    for arm in arms:
        arm["baseline_comparison"] = {
            "price_nll_delta": arm["price"]["nll"] - baselines["price"]["nll"],
            "price_brier_delta": arm["price"]["brier_sum_seven_classes"] - baselines["price"]["brier_sum_seven_classes"],
            "raw_action_nll_delta": arm["raw_action"]["nll"] - baselines["action"]["nll"],
            "train_temperature_action_nll_delta": arm["train_temperature_action"]["nll"] - baselines["action"]["nll"],
            "price_nll_delta_from_parent": arm["price"]["nll"] - parent_price["nll"]}
    no_effect = all(c["exact_payload_equality"] for c in comparisons.values()) and all(
        a["training"]["clipped_steps"] == a["training"]["rollback"] == a["training"]["max_shared_trainable_tensors"] == 0
        and a["training"]["parameter_changes_from_initialization"]["price"]["changed_coordinates"] > 0 for a in arms)
    return {"schema": "heptatrader.action-ablation-posthoc-diagnostics.v1",
            "mechanical_status": result["status"], "evidence_kind": "RETROSPECTIVE_PREVIOUSLY_EXAMINED_HOLDOUT",
            "action_label": "FROZEN_PARENT_PRICE_CLASSIFICATION_CORRECTNESS_ACT_INDEX_0_ESCALATE_INDEX_1",
            "hypothesis_outcome": "NO_PRICE_UPDATE_EFFECT_OBSERVED" if no_effect else "REQUIRES_REVIEW",
            "production_eligible": False, "prospective_evidence_available": False,
            "training_or_forward_passes_executed": 0,
            "temperature_usage": "TRAIN_MARGIN_P90_SCORE_DIAGNOSTIC_ONLY_NOT_VALIDATED_CALIBRATION",
            "source_unchanged": True, "result_sha256": manifest[str(out / "result.json")],
            "parent_holdout_recorded": result["parent_holdout"], "parent_price_recomputed": parent_price,
            "train_prior": baselines, "arms": arms, "price_tensor_comparisons_to_price_only": comparisons,
            "nonzero_weight_action_tensor_comparison": action_comparison,
            "nonzero_weight_prediction_arrays_equal": prediction_equality,
            "historical_quality": {
                "all_arms_price_nll_worse_than_train_prior": all(a["baseline_comparison"]["price_nll_delta"] > 0 for a in arms),
                "all_arms_raw_action_nll_worse_than_train_prior": all(a["baseline_comparison"]["raw_action_nll_delta"] > 0 for a in arms),
                "all_arms_train_temperature_action_nll_worse_than_train_prior": all(a["baseline_comparison"]["train_temperature_action_nll_delta"] > 0 for a in arms)},
            "input_manifest_sha256": manifest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-directory", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.result_directory, args.source)
    require(not args.output.exists() and args.output.parent.is_dir(), "NEW_OUTPUT_REQUIRED")
    import os
    with args.output.open("x") as stream:
        json.dump(report, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    args.output.chmod(0o400)
    print(json.dumps({"output": str(args.output), "sha256": sha256(args.output),
                      "hypothesis_outcome": report["hypothesis_outcome"], "historical_quality": report["historical_quality"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
