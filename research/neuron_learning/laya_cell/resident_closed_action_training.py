"""Frozen genuine parent-correctness data for a separate ACT adapter phase.

Only already closed, originally recorded forward observations supply labels.
The PRICE population is never filtered by these labels. Balanced ACTION pairs
are unique observations; replaying epochs does not increase their unique count.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path

from .integrity import ContractError, canonical, digest, read_json, sha_file
from .resident_action_v2_closed_source import collect_parent_closed_train, check_train_source


SCHEMA = "heptatrader.laya-closed-action-training.v1"
MAX_PAIRS = 20
POLICY = {
    "selection": "MINORITY_CHRONOLOGY_THEN_NEAREST_UNIQUE_OPPOSITE_LABEL",
    "max_pairs": MAX_PAIRS,
    "minimum_observed_rows": 16,
    "minimum_each_label": 4,
    "price_population_filtered_by_action_labels": False,
    "action_gradient_target": "ACT_ADAPTER_ONLY_SEPARATE_OPTIMIZER",
    "labels_present_in_model_input": False,
    "repeated_presentations_are_new_independent_rows": False,
    "model_or_trade_permission": False,
}
ACTION_BALANCE = "DISJOINT_ONE_ACT_ONE_ESCALATE_UNWEIGHTED_CE"
ACTION_DISTRIBUTION = {
    "gradient_population": "DISJOINT_LABEL_BALANCED_CLOSED_PARENT_CORRECTNESS_PAIRS",
    "temperature_population": "FROZEN_SELECTED_CLOSED_PARENT_ROWS",
    "natural_population_correct_probability_calibrated": False,
    "execution_success_probability": False,
    "future_original_observation_validation_required": True,
}
MAXIMUM_SOURCE_BYTES = 32 * 1024**2


def _ordered(rows):
    return sorted(rows, key=lambda row: (row["anchor_ms"], row["row_id"]))


def _spaced(rows, count):
    if count == 1:
        return [rows[-1]]
    return [rows[(len(rows) - 1) * i // (count - 1)] for i in range(count)]


def _selection(rows):
    """Match both genuine labels in time without reuse or changing PRICE rows."""
    groups = {True: _ordered([r for r in rows if r["parent_correct"]]),
              False: _ordered([r for r in rows if not r["parent_correct"]])}
    if (len(rows) < POLICY["minimum_observed_rows"] or
            min(len(group) for group in groups.values()) < POLICY["minimum_each_label"]):
        raise ContractError("CLOSED_ACTION_INSUFFICIENT_GENUINE_SUPPORT")
    minority = True if len(groups[True]) <= len(groups[False]) else False
    chosen = _spaced(groups[minority], min(MAX_PAIRS, len(groups[minority])))
    remaining = list(groups[not minority])
    pairs = []
    for row in chosen:
        opposite = min(remaining, key=lambda value: (
            abs(value["anchor_ms"] - row["anchor_ms"]),
            value["anchor_ms"], value["row_id"]))
        remaining.remove(opposite)
        pairs.append([row["row_id"], opposite["row_id"]] if minority
                     else [opposite["row_id"], row["row_id"]])
    pairs.sort(key=lambda pair: next(r["anchor_ms"] for r in rows if r["row_id"] == pair[0]))
    ids = {key for pair in pairs for key in pair}
    # Keep the original minimum of sixteen calibration rows even when there
    # are only four minority examples. Extra majority rows calibrate only;
    # only the disjoint pairs below produce ACTION gradients.
    calibration = [r for r in _ordered(rows) if r["row_id"] in ids]
    if len(calibration) < POLICY["minimum_observed_rows"]:
        extra = _ordered([r for r in rows if r["row_id"] not in ids])
        calibration += _spaced(extra, POLICY["minimum_observed_rows"] - len(calibration))
        calibration = _ordered(calibration)
    return pairs, [r["row_id"] for r in calibration]


def _original_pins(root, rows, selected_ids):
    directories = {}
    for path in sorted(root.glob("*/plan.json")):
        plan = read_json(path)
        key = plan.get("plan_id")
        if key in directories:
            raise ContractError("CLOSED_ACTION_DUPLICATE_PLAN_ID")
        directories[key] = path.parent
    paths = set()
    for row in rows:
        if row["row_id"] not in selected_ids:
            continue
        directory = directories[row["plan_id"]]
        for name in ("plan.json", "qualification.json",
                     "prediction-%03d.json" % row["slot"],
                     "outcome-%03d.json" % row["slot"]):
            paths.add(directory / name)
    # Each selected original still receives a complete SHA in this call. A
    # shared plan/qualification needs one read, not one for every selected row.
    # These versions only guard the just-read bytes during this call; no proof
    # or file identity is reused by a later source/candidate verification.
    def version(path):
        value = path.stat()
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                value.st_ctime_ns, value.st_mode, value.st_uid, value.st_gid,
                value.st_nlink)
    files = {}
    versions = {}
    for path in sorted(paths):
        before = version(path)
        files[str(path)] = sha_file(path)
        if version(path) != before:
            raise ContractError("CLOSED_ACTION_ORIGINAL_FILE_CHANGED")
        versions[path] = before
    if any(version(path) != before for path, before in versions.items()):
        raise ContractError("CLOSED_ACTION_ORIGINAL_FILE_CHANGED")
    return [{"path": path, "sha256": sha} for path, sha in sorted(files.items())]


def freeze_source(evaluation_root, parent_id, cutoff_ms):
    """Freeze a newly requested ACTION population; never change old trials."""
    root = Path(evaluation_root).resolve(strict=True)
    closed = collect_parent_closed_train(root, parent_id, cutoff_ms)
    check_train_source(closed)
    pairs, calibration_ids = _selection(closed["rows"])
    value = {"schema": SCHEMA, "evaluation_root": str(root),
             "parent_id": parent_id, "training_cutoff_ms": cutoff_ms,
             "policy": deepcopy(POLICY), "closed_source": closed,
             "pair_row_ids": pairs, "calibration_row_ids": calibration_ids,
             "original_file_pins": _original_pins(root, closed["rows"], set(calibration_ids)),
             "broker_orders_sent": 0, "model_or_trade_permission": False}
    value["source_id"] = digest(value)
    check_source(value)
    return value


def check_source(source, *, verify_originals=True):
    if (not isinstance(source, dict) or source.get("schema") != SCHEMA or
            source.get("source_id") != digest({k: v for k, v in source.items() if k != "source_id"}) or
            source.get("policy") != POLICY or source.get("broker_orders_sent") != 0 or
            source.get("model_or_trade_permission") is not False):
        raise ContractError("CLOSED_ACTION_SOURCE_CHANGED")
    manifest = check_train_source(source["closed_source"])
    if (manifest["frozen_parent_id"] != source.get("parent_id") or
            manifest["training_cutoff_ms"] != source.get("training_cutoff_ms")):
        raise ContractError("CLOSED_ACTION_PARENT_OR_CUTOFF_CHANGED")
    pairs, ids = _selection(source["closed_source"]["rows"])
    if pairs != source.get("pair_row_ids") or ids != source.get("calibration_row_ids"):
        raise ContractError("CLOSED_ACTION_SELECTION_CHANGED")
    if verify_originals:
        root = Path(source["evaluation_root"])
        current = collect_parent_closed_train(root, source["parent_id"], source["training_cutoff_ms"])
        # New future pending directories do not change a frozen training set.
        # Any added historical row, changed original, or backfill fails closed.
        if current["rows"] != source["closed_source"]["rows"]:
            raise ContractError("CLOSED_ACTION_ORIGINAL_POPULATION_CHANGED")
        pins = _original_pins(root, current["rows"], set(ids))
        if pins != source.get("original_file_pins"):
            raise ContractError("CLOSED_ACTION_ORIGINAL_FILE_CHANGED")
    return source


def write_source(path, source):
    """Write once and pin complete bytes; callers store the SHA in the new job."""
    check_source(source)
    path = Path(path)
    with path.open("xb") as stream:
        stream.write(canonical(source) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o400)
    return sha_file(path)


def load_source(path, expected_sha256, *, parent_id, cutoff_ms, evaluation_root):
    path = Path(path)
    if sha_file(path) != expected_sha256:
        raise ContractError("CLOSED_ACTION_SOURCE_FILE_CHANGED")
    source = read_json(path, MAXIMUM_SOURCE_BYTES)
    check_source(source)
    if (source["parent_id"] != parent_id or source["training_cutoff_ms"] != cutoff_ms or
            source["evaluation_root"] != str(Path(evaluation_root).resolve(strict=True))):
        raise ContractError("CLOSED_ACTION_JOB_BINDING_CHANGED")
    return source


def calibration_rows(source):
    check_source(source)
    by_id = {row["row_id"]: row for row in source["closed_source"]["rows"]}
    return [deepcopy(by_id[key]) for key in source["calibration_row_ids"]]


def batches(source):
    check_source(source)
    by_id = {row["row_id"]: row for row in source["closed_source"]["rows"]}
    return [[deepcopy(by_id[key]) for key in pair] for pair in source["pair_row_ids"]]


def prepare_profile(source, parent_action_logits):
    """Temperature and support use the same frozen, causally closed rows."""
    from .resident_action_v2_research import decide_train_only_action
    rows = calibration_rows(source)
    metadata = [{"row_id": r["row_id"], "anchor_ms": r["anchor_ms"],
                 "future_outcome_available_ms": r["future_outcome_available_ms"],
                 "parent_correct": r["parent_correct"]} for r in rows]
    profile = decide_train_only_action(metadata, cutoff_ms=source["training_cutoff_ms"],
                                       parent_action_logits=parent_action_logits)
    profile.pop("profile_id")
    profile.update(batch_balance_strategy=ACTION_BALANCE,
                   closed_action_source_id=source["source_id"],
                   action_gradient_target=POLICY["action_gradient_target"],
                   action_distribution_scope=deepcopy(ACTION_DISTRIBUTION),
                   price_population_filtered_by_action_labels=False)
    profile["profile_id"] = digest(profile)
    return profile


def check_profile_source(source, profile):
    """A self-consistent profile must still match the actual frozen labels."""
    from .resident_action_v2_research import _check_profile
    _check_profile(profile)
    by_id = {row["row_id"]: row for row in source["closed_source"]["rows"]}
    ids = source["calibration_row_ids"]
    correct = sum(by_id[key]["parent_correct"] for key in ids)
    if (profile.get("closed_action_source_id") != source["source_id"] or
            profile.get("train_cutoff_ms") != source["training_cutoff_ms"] or
            profile.get("training_rows") != len(ids) or
            profile.get("training_row_ids_digest") != digest(ids) or
            profile.get("labels") != {"ACT_correct": correct, "ESCALATE_wrong": len(ids)-correct} or
            profile.get("action_optimizer_admitted") is not True or
            profile.get("batch_balance_strategy") != ACTION_BALANCE or
            profile.get("action_gradient_target") != POLICY["action_gradient_target"] or
            profile.get("action_distribution_scope") != ACTION_DISTRIBUTION or
            profile.get("price_population_filtered_by_action_labels") is not False or
            profile.get("action_temperature_training_only") != profile.get("training_p90_abs_action_logit_margin")):
        raise ContractError("CLOSED_ACTION_PROFILE_SOURCE_CHANGED")
    return profile


def auxiliary_action_loss(action_logits, parent_correct, profile):
    """Paired ACT CE for the ACT-only optimizer, never a PRICE gradient."""
    import torch
    from torch.nn import functional as F
    from .resident_action_v2_research import _check_profile, ACTION_WEIGHT_CAP
    _check_profile(profile)
    if (profile.get("action_gradient_target") != POLICY["action_gradient_target"] or
            profile.get("batch_balance_strategy") != ACTION_BALANCE or
            not profile["action_optimizer_admitted"] or
            not torch.is_tensor(action_logits) or tuple(action_logits.shape) != (2, 2) or
            not action_logits.is_floating_point() or not torch.isfinite(action_logits).all() or
            not torch.is_tensor(parent_correct) or tuple(parent_correct.shape) != (2,) or
            parent_correct.dtype != torch.bool or int(parent_correct.sum()) != 1):
        raise ContractError("CLOSED_ACTION_PAIRED_LOSS_INPUT")
    return ACTION_WEIGHT_CAP * F.cross_entropy(
        action_logits / profile["action_temperature_training_only"], (~parent_correct).long())
