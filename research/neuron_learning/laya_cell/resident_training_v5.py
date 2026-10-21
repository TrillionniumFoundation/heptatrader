"""New future-only policy: unchanged PRICE episode plus isolated genuine ACT.

Historical v1-v4 jobs retain their exact receipts. The new ACTION optimizer has
its own budget and never changes PRICE parameters or the frozen encoder.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path

from .integrity import ContractError, digest, read_json, sha_file
from . import resident_training_v4 as previous
from . import resident_closed_action_training as closed_action

SCHEMA = "heptatrader.laya-resident-objective.v5"
ACTION_PHASE_SCHEMA = "heptatrader.laya-closed-action-phase.v1"
PRICE_OUTPUT = "FROZEN_SAME_PRICE_TRAIN_ROWS_CALIBRATION_V1"
ACTION_PHASE = {
    "epochs": 4, "max_pairs": 20, "max_unique_rows": 40,
    "max_optimizer_steps": 80, "optimizer": "SEPARATE_ACT_ONLY_AdamW",
    "loss_weight": 0.1, "gradient_norm_cap": 1.0,
    "price_parameters_updated": False, "encoder_parameters_updated": False,
    "repeated_presentations_are_new_independent_rows": False,
}
EPISODE = previous.EPISODE
ROW_ACCOUNTING_SCHEMA = previous.ROW_ACCOUNTING_SCHEMA
SELECTION = previous.SELECTION
ACTION_OUTPUT = previous.ACTION_OUTPUT
ACTION_BALANCE = previous.ACTION_BALANCE
PRIMARY_HORIZON_SECONDS = previous.PRIMARY_HORIZON_SECONDS
OBJECTIVE = {
    **deepcopy(previous.OBJECTIVE), "schema": SCHEMA,
    "closed_action_phase": deepcopy(ACTION_PHASE),
    "price_output_profile": PRICE_OUTPUT,
    "price_calibration_source": "EXACT_SAME_PRICE_TRAINING_ROWS_FINAL_CHILD_LOGITS",
    "price_calibration_hyperparameters": "FIXED_IN_PINNED_RUNTIME_NOT_FUTURE_SELECTED",
}
RUNTIME_PINS = tuple(dict.fromkeys(previous.RUNTIME_PINS + (
    "learn/laya_cell/resident_training_v5.py",
    "learn/laya_cell/resident_closed_action_training.py",
    "learn/laya_cell/resident_action_v2_closed_source.py",
    "learn/laya_cell/resident_price_calibration.py",
    "learn/laya_cell/resident_train.py", "learn/laya_cell/resident_predict.py",
    "learn/laya_cell/resident_evaluate.py",
    "learn/laya_cell/resident.py", "learn/laya_cell/resident_launch.py",
    "learn/laya_cell/resident_row_accounting.py",
    "learn/laya_cell/resident_plan_admission.py")))
select_covered_rows = previous.select_covered_rows
primary_rows = previous.primary_rows
timed_rows = previous.timed_rows
coverage = previous.coverage
action_inference = previous.action_inference
schedule = previous.schedule
receipt = previous.receipt
point = previous.point
curve = previous.curve


def enabled(value):
    return isinstance(value, dict) and value.get("schema") == SCHEMA


def check_objective(value):
    if value != OBJECTIVE:
        raise ContractError("RESIDENT_V5_OBJECTIVE_CHANGED")
    return value


def _price_job(job):
    # This is an in-memory validation projection, never a rewritten job or
    # artifact. Reuse all existing PRICE receipt/curve checks unchanged.
    result = deepcopy(job)
    result["objective"] = deepcopy(previous.OBJECTIVE)
    return result


def _source_metadata(config):
    required = ("closed_action_source", "closed_action_source_sha256",
                "closed_action_source_id", "closed_action_parent_id",
                "closed_action_training_cutoff_ms")
    if any(key not in config for key in required):
        raise ContractError("CLOSED_ACTION_CONFIG_REQUIRED")
    path = Path(config["closed_action_source"])
    if (not path.is_absolute() or type(config["closed_action_training_cutoff_ms"]) is not int or
            config["closed_action_training_cutoff_ms"] <= 0 or
            sha_file(path) != config["closed_action_source_sha256"]):
        raise ContractError("CLOSED_ACTION_CONFIG_SOURCE_CHANGED")
    source = read_json(path, closed_action.MAXIMUM_SOURCE_BYTES)
    closed_action.check_source(source, verify_originals=False)
    if (source["source_id"] != config["closed_action_source_id"] or
            source["parent_id"] != config["closed_action_parent_id"] or
            source["training_cutoff_ms"] != config["closed_action_training_cutoff_ms"] or
            source["evaluation_root"] != str(Path(config["evaluation_directory"]).resolve(strict=True))):
        raise ContractError("CLOSED_ACTION_CONFIG_BINDING_CHANGED")
    return source


def check_config(config):
    check_objective(config.get("objective"))
    source = _source_metadata(config)
    parent = read_json(Path(config["initial_parent_directory"]) / "candidate.json")
    if parent.get("candidate_id") != source["parent_id"]:
        raise ContractError("CLOSED_ACTION_CONFIG_PARENT_CHANGED")
    return source


def check_job(job):
    check_objective(job.get("objective"))
    ids = previous.check_job(_price_job(job))
    source = _source_metadata(job)
    if (source["parent_id"] != job.get("parent_id") or
            source["training_cutoff_ms"] > job["window"]["training_end_ms"]):
        raise ContractError("CLOSED_ACTION_JOB_PARENT_OR_FUTURE_CUTOFF")
    return ids


def source_for_job(job):
    check_job(job)
    return closed_action.load_source(job["closed_action_source"], job["closed_action_source_sha256"],
        parent_id=job["parent_id"], cutoff_ms=job["closed_action_training_cutoff_ms"],
        evaluation_root=job["evaluation_directory"])


def context(job):
    check_job(job)
    value = previous.context(_price_job(job))
    value.update(objective_digest=digest(job["objective"]),
                 closed_action_source_id=job["closed_action_source_id"],
                 closed_action_source_sha256=job["closed_action_source_sha256"])
    return value


def freeze(job, batches, targets, calibration, price_baselines):
    value = previous.freeze(_price_job(job), batches, targets, calibration, price_baselines)
    value.pop("episode_id")
    value.update(context(job))
    value["episode_id"] = digest(value)
    return value


def action_receipt(source, steps, profile, *, price_parameters_unchanged,
                   encoder_parameters_unchanged):
    closed_action.check_source(source)
    closed_action.check_profile_source(source, profile)
    pairs = source["pair_row_ids"]
    if (profile.get("closed_action_source_id") != source["source_id"] or
            price_parameters_unchanged is not True or encoder_parameters_unchanged is not True or
            not isinstance(steps, list) or len(steps) != len(pairs) * ACTION_PHASE["epochs"] or
            len(steps) > ACTION_PHASE["max_optimizer_steps"]):
        raise ContractError("CLOSED_ACTION_PHASE_CONTEXT")
    for index, step in enumerate(steps):
        if (step.get("epoch") != index // len(pairs) + 1 or
                step.get("batch_in_epoch") != index % len(pairs) + 1 or
                step.get("closed_action_row_ids") != pairs[index % len(pairs)] or
                type(step.get("applied")) is not bool or
                type(step.get("action_loss")) not in (int, float) or
                not math.isfinite(step["action_loss"]) or step["action_loss"] < 0 or
                type(step.get("gradient_digest")) is not str or
                len(step["gradient_digest"]) != 64 or
                any(c not in "0123456789abcdef" for c in step["gradient_digest"])):
            raise ContractError("CLOSED_ACTION_PHASE_STEP_CHANGED")
    ids = [key for pair in pairs for key in pair]
    if len(ids) != len(set(ids)) or len(ids) > ACTION_PHASE["max_unique_rows"]:
        raise ContractError("CLOSED_ACTION_PHASE_DUPLICATE_ROW")
    return {"schema": ACTION_PHASE_SCHEMA, "source_id": source["source_id"],
            "profile": deepcopy(ACTION_PHASE), "unique_row_ids": ids,
            "unique_rows": len(ids), "steps": deepcopy(steps),
            "attempted_steps": len(steps), "applied_steps": sum(s["applied"] for s in steps),
            "rolled_back_steps": sum(not s["applied"] for s in steps),
            "gradient_presentations": 2 * len(steps),
            "price_parameters_unchanged": True, "encoder_parameters_unchanged": True,
            "repeated_presentations_are_new_independent_rows": False}


def verify_candidate(job, candidate):
    check_job(job)
    if candidate.get("objective") != job["objective"]:
        raise ContractError("V5_CANDIDATE_OBJECTIVE_CHANGED")
    source = source_for_job(job)
    profile = candidate.get("action_training_profile")
    if (candidate.get("closed_action_source_id") != source["source_id"] or
            candidate.get("closed_action_source_sha256") != job["closed_action_source_sha256"]):
        raise ContractError("V5_CANDIDATE_CLOSED_SOURCE_CHANGED")
    phase = candidate.get("action_phase", {})
    rebuilt = action_receipt(source, phase.get("steps"), profile,
        price_parameters_unchanged=phase.get("price_parameters_unchanged"),
        encoder_parameters_unchanged=phase.get("encoder_parameters_unchanged"))
    if phase != rebuilt or candidate.get("action_update_steps") != rebuilt["applied_steps"]:
        raise ContractError("V5_CANDIDATE_ACTION_RECEIPT_CHANGED")
    legacy = deepcopy(candidate)
    legacy["objective"] = deepcopy(previous.OBJECTIVE)
    frozen = legacy["training_episode"]
    if (frozen.get("closed_action_source_id") != source["source_id"] or
            frozen.get("closed_action_source_sha256") != job["closed_action_source_sha256"] or
            frozen.get("objective_digest") != digest(job["objective"])):
        raise ContractError("V5_CANDIDATE_FROZEN_CONTEXT_CHANGED")
    if frozen.get("episode_id") != digest({k: v for k, v in frozen.items() if k != "episode_id"}):
        raise ContractError("V5_CANDIDATE_FROZEN_EPISODE_CHANGED")
    frozen["objective_digest"] = digest(previous.OBJECTIVE)
    frozen["episode_id"] = digest({k: v for k, v in frozen.items() if k != "episode_id"})
    legacy["learning_curve"]["episode_id"] = frozen["episode_id"]
    previous.verify_candidate(_price_job(job), legacy)
    from .resident_price_calibration import validate, verify_candidate_fit
    calibration = validate(candidate.get("price_calibration"))
    if (candidate.get("price_output_profile") != PRICE_OUTPUT or
            calibration["parent_id"] != job["parent_id"] or
            calibration["adapter_sha256"] != candidate["adapter_sha256"] or
            calibration["training_cutoff_ms"] != job["window"]["training_end_ms"] or
            calibration["training_row_ids_digest"] != candidate["price_baselines"]["training_row_ids_digest"]):
        raise ContractError("V5_CANDIDATE_PRICE_CALIBRATION_CHANGED")
    verify_candidate_fit(job, candidate)


def configuration_for_release(config, runtime_directory, *, closed_action_source,
                               closed_action_source_sha256=None):
    result = previous.configuration_for_release(config, runtime_directory)
    runtime = Path(result["runtime_directory"])
    for name in RUNTIME_PINS:
        result["runtime_sha256"][name] = sha_file(runtime / name)
    path = Path(closed_action_source).resolve(strict=True)
    sha = sha_file(path)
    if closed_action_source_sha256 is not None and sha != closed_action_source_sha256:
        raise ContractError("CLOSED_ACTION_REQUESTED_SOURCE_CHANGED")
    source = read_json(path, closed_action.MAXIMUM_SOURCE_BYTES)
    closed_action.check_source(source)
    result.update(objective=deepcopy(OBJECTIVE), closed_action_source=str(path),
        closed_action_source_sha256=sha, closed_action_source_id=source["source_id"],
        closed_action_parent_id=source["parent_id"],
        closed_action_training_cutoff_ms=source["training_cutoff_ms"])
    check_config(result)
    return result
