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
ACTION_NUMERIC_SCHEMA = "heptatrader.v5-act-adapter-change.v1"
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


def _checked_job(job):
    check_objective(job.get("objective"))
    ids = previous.check_job(_price_job(job))
    source = _source_metadata(job)
    if (source["parent_id"] != job.get("parent_id") or
            source["training_cutoff_ms"] > job["window"]["training_end_ms"]):
        raise ContractError("CLOSED_ACTION_JOB_PARENT_OR_FUTURE_CUTOFF")
    return ids, source


def check_job(job):
    return _checked_job(job)[0]


def source_for_job(job):
    _, source = _checked_job(job)
    # The same call has already read, hashed and bound these exact bytes.
    # Replay originals once without reopening the identical source manifest.
    return closed_action.check_source(source)


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
                   encoder_parameters_unchanged, verify_originals=True):
    closed_action.check_source(source, verify_originals=verify_originals)
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


def verify_action_update(job, candidate):
    """Read small pinned adapters, never torch/base or a claimed step alone.

    An original scorer-only parent has zero ACT effective update because its
    newly initialized B is zero. Its random A is not invented or reconstructed.
    This proves a numeric adapter change, not future correctness or trade value.
    """
    import numpy as np
    from safetensors import safe_open
    from .integrity import validate_candidate_artifact
    applied = candidate.get("action_phase", {}).get("applied_steps")
    if (type(applied) is not int or applied <= 0 or
            type(candidate.get("action_update_steps")) is not int or
            candidate.get("action_update_steps") != applied or
            candidate.get("action_head_trained") is not True):
        raise ContractError("V5_ACT_NO_APPLIED_NUMERIC_UPDATE")
    parent_dir = Path(job["parent_directory"])
    parent_manifest = parent_dir/"candidate.json"
    if sha_file(parent_manifest) != job["parent_manifest_sha256"]:
        raise ContractError("V5_ACT_PARENT_MANIFEST_CHANGED")
    parent = validate_candidate_artifact(parent_dir)
    if parent["candidate_id"] != job["parent_id"] or candidate.get("parent_id") != job["parent_id"]:
        raise ContractError("V5_ACT_PARENT_ID_CHANGED")
    final_path = Path(job["output_directory"])/"adapter.safetensors"
    if sha_file(final_path) != candidate.get("adapter_sha256"):
        raise ContractError("V5_ACT_FINAL_ADAPTER_CHANGED")
    keys = ("model.act_head.2.A", "model.act_head.2.B")
    with safe_open(str(parent_dir/"adapter.safetensors"), framework="np") as parent_file, \
            safe_open(str(final_path), framework="np") as final_file:
        parent_keys, final_keys = set(parent_file.keys()), set(final_file.keys())
        if (sorted(final_keys) != candidate.get("tensor_mask") or
                {name for name in final_keys if ".act_head." in name} != set(keys) or
                {name for name in parent_keys if ".act_head." in name} not in (set(), set(keys))):
            raise ContractError("V5_ACT_ADAPTER_TENSOR_MASK")
        # Read just the two tiny ACT factors. Header inspection does not load
        # scorer tensors, a pretrained base, torch or any GPU state.
        previous = {name: parent_file.get_tensor(name) for name in keys if name in parent_keys}
        final = {name: final_file.get_tensor(name) for name in keys}
    for name, shape in zip(keys, ((1, 64), (2, 1))):
        for tensors in (final, previous):
            if name not in tensors:
                continue
            value = tensors[name]
            if value.dtype != np.float32 or value.shape != shape or not np.isfinite(value).all():
                raise ContractError("V5_ACT_ADAPTER_SHAPE_DTYPE_OR_NUMBER")
    statistics = candidate.get("changed_by_tensor")
    if (not isinstance(statistics, dict) or
            any(type(statistics.get(name)) is not int or
                not 0 <= statistics[name] <= final[name].size for name in keys) or
            sum(statistics[name] for name in keys) <= 0):
        raise ContractError("V5_ACT_CHANGED_STATISTICS_REQUIRED")
    has_parent_action = all(name in previous for name in keys)
    changes = {name: (int(np.count_nonzero(final[name] != previous[name]))
                      if has_parent_action else
                      int(np.count_nonzero(final[name])) if name.endswith(".B") else None)
               for name in keys}
    if any(changes[name] is not None and changes[name] != statistics[name] for name in keys):
        raise ContractError("V5_ACT_CHANGED_STATISTICS_MISMATCH")
    with np.errstate(over="ignore", invalid="ignore"):
        effect = final[keys[1]] @ final[keys[0]]
        prior_effect = (previous[keys[1]] @ previous[keys[0]] if has_parent_action
                        else np.zeros((2, 64), dtype=np.float32))
    if not np.isfinite(effect).all() or not np.isfinite(prior_effect).all():
        raise ContractError("V5_ACT_EFFECT_NONFINITE")
    if np.array_equal(effect, prior_effect):
        raise ContractError("V5_ACT_EFFECT_UNCHANGED")
    if (sha_file(parent_manifest) != job["parent_manifest_sha256"] or
            sha_file(parent_dir/"adapter.safetensors") != parent["adapter_sha256"] or
            sha_file(final_path) != candidate["adapter_sha256"]):
        raise ContractError("V5_ACT_ADAPTER_CHANGED_DURING_REPLAY")
    delta = effect.astype(np.float64)-prior_effect.astype(np.float64)
    return {"schema": ACTION_NUMERIC_SCHEMA,
            "parent_manifest_sha256": job["parent_manifest_sha256"],
            "parent_adapter_sha256": parent["adapter_sha256"],
            "final_adapter_sha256": candidate["adapter_sha256"],
            "parent_action_adapter_present": has_parent_action,
            "missing_parent_action_effect": None if has_parent_action else "ZERO_B_INITIALIZATION",
            "actual_factor_changed_counts": changes,
            "missing_parent_A_changed_count_claimed": False,
            "effective_changed_coordinates": int(np.count_nonzero(delta)),
            "effective_max_absolute_change": float(np.abs(delta).max()),
            "effective_delta_digest": digest(delta.tolist()),
            "applied_steps": applied, "gpu_inference_replayed": False,
            "future_value_proven": False, "model_or_trading_authority": False}


def _verify_training(job, candidate, source, *, source_originals_verified=False):
    if candidate.get("objective") != job["objective"]:
        raise ContractError("V5_CANDIDATE_OBJECTIVE_CHANGED")
    profile = candidate.get("action_training_profile")
    if (candidate.get("closed_action_source_id") != source["source_id"] or
            candidate.get("closed_action_source_sha256") != job["closed_action_source_sha256"]):
        raise ContractError("V5_CANDIDATE_CLOSED_SOURCE_CHANGED")
    phase = candidate.get("action_phase", {})
    rebuilt = action_receipt(source, phase.get("steps"), profile,
        price_parameters_unchanged=phase.get("price_parameters_unchanged"),
        encoder_parameters_unchanged=phase.get("encoder_parameters_unchanged"),
        verify_originals=not source_originals_verified)
    if phase != rebuilt or candidate.get("action_update_steps") != rebuilt["applied_steps"]:
        raise ContractError("V5_CANDIDATE_ACTION_RECEIPT_CHANGED")
    if candidate.get("action_adapter_validation") != verify_action_update(job, candidate):
        raise ContractError("V5_ACT_NUMERIC_RECEIPT_CHANGED")
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


def verify_candidate(job, candidate):
    check_job(job)
    _verify_training(job, candidate, source_for_job(job), source_originals_verified=True)
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
