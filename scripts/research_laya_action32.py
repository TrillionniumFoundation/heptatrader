#!/usr/bin/env python3
"""Execute a frozen Pocket4 ACTION32 diagnostic with the existing model and lease.

Default is a read-only preflight. --execute uses the original paired rollout's
verification and heavy-worker admission. It creates no resident job or trial.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from collections import Counter

PROTOCOL_SCHEMA = "heptatrader.action32-controlled-research-protocol.v1"
ROLLOUT_VERIFIER_SHA = "a813db6d5b9fadd013659379d09fbb77eb2e3561a29ddfc66f4f1ec12df9aeff"
PARAMETERS = ["model.act_head.2.A", "model.act_head.2.B"]
SHAPES = [[1, 64], [2, 1]]
ALGORITHMS = (
    "dataset.py", "model.py", "resident_action_v2_research.py",
    "resident_model.py", "resident_train.py",
    "resident_training_v2.py", "resident_training_v3.py",
)


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def bound(path, expected):
    need(sha(path) == expected, "BOUND_FILE_CHANGED:" + str(path))
    return read(path)


def write_once(path, value):
    with Path(path).open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    Path(path).chmod(0o400)


def verify_code(p):
    need(set(p["code_files_sha256"]) == set(ALGORITHMS), "ALGORITHM_PIN_SET")
    root = Path(p["code_root"]) / "research/neuron_learning/laya_cell"
    for name, expected in p["code_files_sha256"].items():
        need(sha(root / name) == expected, "PINNED_ALGORITHM_CHANGED:" + name)


def validate_selection(p, training, holdout, source):
    """Verify the frozen selection; never select or replace rows."""
    s = p["source"]
    need(s["train_end_ms"] + 300_000 <= s["hold_start_ms"]
         < s["hold_end_ms"] <= s["source_cutoff_ms"], "CHRONOLOGICAL_SPLIT")
    need(len(training) == 32 and len(holdout) == 24, "FROZEN_ROW_COUNTS")
    need(sum(r["parent_correct"] is True for r in training) == 8, "ACT_SUPPORT")
    pool = {r["row_id"]: r for r in source["rows"]}
    need(len(pool) == len(source["rows"]), "DUPLICATE_SOURCE_ROW_ID")
    excluded = set(source["excluded_plan_ids"])
    seen_ids, seen_anchors = set(), set()
    for population, rows in (("train", training), ("holdout", holdout)):
        for r in rows:
            need(r == pool.get(r["row_id"]), "SELECTED_ROW_DIFFERS_FROM_FROZEN_SOURCE")
            need(r["row_id"] not in seen_ids, "DUPLICATE_OR_CROSS_SPLIT_ROW")
            seen_ids.add(r["row_id"])
            anchor = (r["state"]["instrument"], r["anchor_ms"], r["state"]["horizon_sec"])
            need(anchor not in seen_anchors and anchor[2] == 300, "DUPLICATE_ANCHOR_OR_HORIZON")
            seen_anchors.add(anchor)
            need(r["parent_id"] == p["parent"]["candidate_id"]
                 and r["plan_id"] not in excluded
                 and r["plan_id"] != s["excluded_plan_id"]
                 and r["broker_orders_sent"] == 0, "ROW_PARENT_OR_SCOPE")
            need(p["parent"]["parent_training_end_ms"] <= r["anchor_ms"]
                 and r["anchor_ms"] + 300_000 <= r["future_outcome_available_ms"],
                 "PARENT_OR_OUTCOME_TIME")
            probabilities = r["frozen_parent_probs"]
            argmax = max(range(7), key=probabilities.__getitem__)
            need(type(r["parent_correct"]) is bool
                 and r["parent_correct"] == (argmax == r["target_class"]),
                 "FROZEN_PARENT_LABEL_CONTRADICTION")
            if population == "train":
                need(r["anchor_ms"] < s["train_end_ms"]
                     and r["future_outcome_available_ms"] <= s["train_end_ms"],
                     "TRAINING_TIME_LEAK")
            else:
                need(s["hold_start_ms"] <= r["anchor_ms"] < s["hold_end_ms"]
                     and r["future_outcome_available_ms"] <= s["source_cutoff_ms"],
                     "HOLDOUT_TIME_RANGE")
    expected_hold = {
        r["row_id"] for r in source["rows"]
        if s["hold_start_ms"] <= r["anchor_ms"] < s["hold_end_ms"]
        and r["future_outcome_available_ms"] <= s["source_cutoff_ms"]
        and r["plan_id"] not in excluded
    }
    need({r["row_id"] for r in holdout} == expected_hold, "HOLDOUT_WAS_FILTERED")
    by_id = {r["row_id"]: r for r in training}
    schedule = p["schedule"]
    need(schedule["attempted_optimizer_steps"] == 64
         and schedule["total_row_presentations"] == 128
         and len(schedule["pairs"]) == 64, "FIXED_UPDATE_BUDGET")
    presented = []
    for attempt, pair in enumerate(schedule["pairs"], 1):
        ids = pair["row_ids"]
        need(pair["attempt"] == attempt and len(ids) == 2
             and all(i in by_id for i in ids), "PAIR_USES_NONTRAINING_ROW")
        need(sum(by_id[i]["parent_correct"] for i in ids) == 1, "PAIR_CLASS_BALANCE")
        presented.extend(ids)
    need(dict(Counter(presented)) == schedule["presentations_by_row_id"]
         and set(presented) == set(by_id), "FROZEN_PRESENTATION_COUNTS")


def checked_protocol(path, expected):
    p = bound(path, expected)
    need(p["schema"] == PROTOCOL_SCHEMA, "PROTOCOL_SCHEMA")
    verify_code(p)
    source = bound(p["source"]["path"], p["source"]["sha256"])
    need(source["schema"] == "heptatrader.price-readout-closed-source.v1"
         and source["horizon_seconds"] == 300
         and digest(source["rows"]) == source["rows_digest"]
         == p["source"]["rows_digest"], "FROZEN_SOURCE_IDENTITY")
    populations = []
    for name in ("training_rows", "holdout_rows"):
        spec = p["selection"][name]
        doc = bound(spec["path"], spec["sha256"])
        need(doc["schema"] == "heptatrader.closed-action-research-rows.v1"
             and doc["source_sha256"] == p["source"]["sha256"], "SELECTION_SOURCE_BINDING")
        populations.append(doc["rows"])
    training, holdout = populations
    validate_selection(p, training, holdout, source)
    parent = p["parent"]
    manifest = bound(Path(parent["directory"]) / "candidate.json",
                     parent["candidate_manifest_sha256"])
    need(manifest["candidate_id"] == parent["candidate_id"], "PARENT_IDENTITY")
    need(sha(Path(parent["directory"]) / "adapter.safetensors")
         == parent["adapter_sha256"] == manifest["adapter_sha256"], "PARENT_ADAPTER_CHANGED")
    base = bound(Path(parent["base_directory"]) / "base-manifest.json",
                 parent["base_manifest_sha256"])
    need(digest(base) == parent["base_id"] == manifest["base_id"], "PARENT_BASE_CHANGED")
    o = p["optimization"]
    reference = bound(o["objective_reference_config"], o["objective_reference_sha256"])
    need(o["parameters"] == PARAMETERS and o["parameter_shapes"] == SHAPES
         and o["trainable_action_parameters"] == 66 and o["optimizer"] == "AdamW"
         and o["preserve_cell_requires_grad_mask"] is True, "ACTION_PARAMETER_PROTOCOL")
    for key in ("learning_rate", "weight_decay", "gradient_norm_cap"):
        need(o[key] == reference["objective"][key], "OPTIMIZER_REFERENCE:" + key)
    need(p["model_execution"]["dtype"] == "float32"
         and p["model_execution"]["relabel_after_replay"] is False
         and p["calibration"]["temperature_value"] is None, "FROZEN_EXECUTION_PROTOCOL")
    return p, training, holdout


def process_snapshot(pid):
    root = Path("/proc") / str(pid)
    return {
        "pid": pid,
        "start_ticks": int((root / "stat").read_text().rsplit(")", 1)[1].split()[19]),
        "argv": [x.decode() for x in (root / "cmdline").read_bytes().split(b"\0") if x],
        "cwd": str((root / "cwd").resolve()),
        "exe": str((root / "exe").resolve()),
    }


def runtime_ready(p):
    """Reuse the original rollout verifier without running its actuator."""
    pre = p["runtime_preconditions"]
    if time.time_ns() // 1_000_000 < pre["not_before_ms"]:
        return {"state": "WAIT", "reason": "PROTOCOL_NOT_BEFORE_TIME"}
    root, target = Path(pre["paired_rollout_root"]), Path(pre["target_runtime"])
    manifest = read(root / "manifest.json")
    need(manifest["plan_sha256"] == pre["original_plan_sha256"]
         and manifest["target_runtime"] == str(target), "ORIGINAL_ROLLOUT_BINDING")
    plan = bound(manifest["plan_path"], pre["original_plan_sha256"])
    q_path = Path(manifest["plan_path"]).parent / "qualification.json"
    status = read(root / "status.json")
    if (status.get("state") != pre["required_rollout_state"]
            or status.get("reason") != "PAIRED_V4_PROCESSES_BOUND"
            or not q_path.exists()):
        return {"state": "WAIT", "reason": "ORIGINAL_TRIAL_OR_PAIRED_V4_PENDING"}
    verifier_path = root / "apply.py"
    need(sha(verifier_path) == ROLLOUT_VERIFIER_SHA, "ORIGINAL_ROLLOUT_VERIFIER_CHANGED")
    spec = importlib.util.spec_from_file_location("_action32_rollout_verifier", verifier_path)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    # terminal() also freezes the entire plan set while waiting for handoff.
    # After COMMITTED, later legitimate trials may exist. Reuse its terminal
    # validator directly; do not freeze the post-handoff plan set.
    verifier.validate_terminal(manifest, plan, read(q_path), time.time_ns() // 1_000_000)
    configs, bindings = {}, {}
    for role in ("trainer", "evaluator"):
        unit = "heptatrader-laya-" + role + ".service"
        wrapper = manifest["wrappers"][role]
        configs[role] = bound(wrapper["path"], wrapper["sha256"])
        binding = verifier.process_binding(manifest, unit, role)
        before = process_snapshot(binding["pid"])
        need(before["cwd"] == str(target.resolve())
             and before["exe"] == str(Path(manifest["python"]).resolve())
             and before["argv"] == [
                 manifest["python"], "-B", "-m", "historical_training.resident_resource_guard",
                 "--config", wrapper["path"], "--role", role,
             ], "PAIRED_PROCESS_IDENTITY:" + role)
        unit_path = Path.home() / ".config/systemd/user" / unit
        need(sha(unit_path) == manifest["services"][unit]["new_sha256"],
             "PAIRED_UNIT_CHANGED:" + role)
        bindings[role] = before
    for role, before in bindings.items():
        need(process_snapshot(before["pid"]) == before, "PAIRED_PID_CHANGED:" + role)
    trainer = configs["trainer"]
    need(trainer["original_config"] == p["optimization"]["objective_reference_config"]
         and trainer["original_config_sha256"] == p["optimization"]["objective_reference_sha256"],
         "PAIRED_TRAINER_OBJECTIVE")
    origin = target / "guard/historical_training/resident_resource_guard.py"
    for config in configs.values():
        for name, expected in config["guard_files_sha256"].items():
            need(Path(name).name == name and sha(origin.parent / name) == expected,
                 "STAGED_GUARD_CHANGED:" + name)
    return {
        "state": "READY", "reason": "ORIGINAL_TRIAL_TERMINAL_AND_PAIRED_V4_BOUND",
        "bindings": bindings, "verifier": verifier, "manifest": manifest,
        "rollout_manifest_sha256": sha(root / "manifest.json"),
        "qualification_sha256": sha(q_path), "guard_config": trainer,
        "guard_origin": str(origin.resolve()), "guard_import_root": str(target / "guard"),
    }


def train_model(p, training, holdout, output, report):
    import torch
    from torch.nn import functional as F
    from safetensors.torch import load_file, save_file
    from laya_cell.model import load_verified_laya, require_pocket4_gpu, tensor_hash
    from laya_cell.resident_model import ResidentDecisionCell, PROFILE
    from laya_cell.dataset import encode_rows
    from laya_cell.resident_action_v2_research import (
        decide_train_only_action, balanced_action_loss, v2_action_inference,
    )
    from laya_cell.resident_training_v3 import pair_calibration

    torch.manual_seed(p["model_execution"]["torch_seed"])
    torch.set_num_threads(2)
    device = require_pocket4_gpu()
    torch.cuda.reset_peak_memory_stats()
    report["torch_execution"] = {
        "version": str(torch.__version__), "hip_version": torch.version.hip,
        "git_version": getattr(torch.version, "git_version", None), "device": str(device),
        "gcn_arch_name": str(getattr(torch.cuda.get_device_properties(0), "gcnArchName", "")),
        "num_threads": torch.get_num_threads(), "seed": p["model_execution"]["torch_seed"],
    }
    parent, o = p["parent"], p["optimization"]
    raw, tokenizer, base_id = load_verified_laya(
        Path(parent["base_directory"]), read(Path(parent["base_directory"]) / "base-manifest.json"))
    need(base_id == parent["base_id"], "LOADED_BASE_CHANGED")
    cell = ResidentDecisionCell(raw, base_id)
    cell.restore_parent(load_file(str(Path(parent["directory"]) / "adapter.safetensors")),
                        allow_scorer_only=True)
    need(PROFILE["global_relative_limit"] == o["max_global_relative_l2"]
         and PROFILE["per_layer_relative_limit"] == o["max_layer_relative_l2"], "LOADED_NORM_PROFILE")
    need(all(t.device == device for t in cell.parameters()), "MODEL_OR_ADAPTER_DEVICE")
    named = dict(cell.named_parameters())
    mask = {n: t.requires_grad for n, t in named.items()}
    parameters = [named[n] for n in PARAMETERS]
    need([list(t.shape) for t in parameters] == SHAPES
         and all(t.requires_grad and t.dtype == torch.float32 for t in parameters)
         and sum(t.numel() for t in parameters) == 66, "LOADED_ACTION_PARAMETERS")
    initial = cell.trainable_state()
    other = {n: tensor_hash(t) for n, t in named.items() if t.requires_grad and n not in PARAMETERS}
    cell.assert_frozen()
    encoding_cache, replayed_parent_price = {}, {}
    report.update(model_forward_calls=0, model_row_presentations=0, steps=[],
                  max_parent_price_probability_delta=0.0, parent_price_probability_rows_checked=0,
                  max_final_vs_replayed_parent_price_probability_delta=0.0,
                  final_price_probability_rows_checked=0)

    def forward(rows, phase="training_pair"):
        key = tuple(r["row_id"] for r in rows)
        if key not in encoding_cache:
            encoding_cache[key] = encode_rows(tokenizer, rows)
        price, action = cell(**{k: v.to(device) for k, v in encoding_cache[key].items()})
        report["model_forward_calls"] += 1
        report["model_row_presentations"] += len(rows)
        need(bool(torch.isfinite(price).all() and torch.isfinite(action).all()), "NONFINITE_MODEL_OUTPUT")
        original = [max(range(7), key=r["frozen_parent_probs"].__getitem__) for r in rows]
        need(price.detach().argmax(-1).cpu().tolist() == original,
             "FROZEN_PARENT_PRICE_ARGMAX_PARITY_FAILED_NO_RELABEL")
        if phase in ("parent_replay", "fixed_final"):
            probabilities = price.detach().softmax(-1).cpu().double()
            if phase == "parent_replay":
                frozen = torch.tensor([r["frozen_parent_probs"] for r in rows], dtype=torch.float64)
                report["max_parent_price_probability_delta"] = max(
                    report["max_parent_price_probability_delta"], float((probabilities - frozen).abs().max()))
                report["parent_price_probability_rows_checked"] += len(rows)
                for row, probability in zip(rows, probabilities):
                    replayed_parent_price[row["row_id"]] = probability.clone()
            else:
                original = torch.stack([replayed_parent_price[r["row_id"]] for r in rows])
                report["max_final_vs_replayed_parent_price_probability_delta"] = max(
                    report["max_final_vs_replayed_parent_price_probability_delta"],
                    float((probabilities - original).abs().max()))
                report["final_price_probability_rows_checked"] += len(rows)
        return price, action

    def replay(rows, phase):
        cell.eval()
        with torch.no_grad():
            return torch.cat([forward([r], phase)[1].detach().cpu() for r in rows], dim=0)

    parent_train_action = replay(training, "parent_replay")
    calibration = pair_calibration(decide_train_only_action(
        training, cutoff_ms=p["source"]["train_end_ms"], parent_action_logits=parent_train_action))
    need(calibration["action_optimizer_admitted"] is True, "TRUE_ACTION_SUPPORT_NOT_ADMITTED")
    parent_hold_action = replay(holdout, "parent_replay")
    need(report["parent_price_probability_rows_checked"] == 56, "PARENT_REPLAY_POPULATION")
    optimizer = torch.optim.AdamW(parameters, lr=o["learning_rate"], weight_decay=o["weight_decay"])
    write_once(output / "calibration.json", {
        "protocol_sha256": report["protocol_sha256"], "calibration": calibration,
        "optimizer_defaults": optimizer.defaults, "torch_execution": report["torch_execution"],
        "resident_model_profile": copy.deepcopy(PROFILE), "resident_model_profile_id": digest(PROFILE),
        "parameter_names": PARAMETERS, "parameter_shapes": SHAPES,
        "actual_action_parameter_dtype": str(parameters[0].dtype),
        "model_execution_protocol": p["model_execution"],
        "schedule_sha256": digest(p["schedule"]),
        "parent_price_replay": {
            "rows": 56, "max_abs_probability_delta": report["max_parent_price_probability_delta"],
            "argmax_parity_passed": True,
        },
    })
    report["calibration"] = calibration
    by_id = {r["row_id"]: r for r in training}
    cell.train(True)
    for pair in p["schedule"]["pairs"]:
        selected = [by_id[i] for i in pair["row_ids"]]
        saved = cell.trainable_state()
        optimizer_before = copy.deepcopy(optimizer.state_dict())
        cell.zero_grad(set_to_none=True)
        price, action = forward(selected)
        truth = torch.tensor([r["target_class"] for r in selected], device=device, dtype=torch.long)
        correct = torch.tensor([r["parent_correct"] for r in selected], device=device, dtype=torch.bool)
        price_reference = F.cross_entropy(price.detach(), truth)
        loss = balanced_action_loss(action, correct, price_reference, calibration)
        loss.backward()
        need(all(t.grad is None for n, t in named.items() if n not in PARAMETERS), "NON_ACTION_GRADIENT")
        gradient_norm = torch.nn.utils.clip_grad_norm_(
            parameters, o["gradient_norm_cap"], error_if_nonfinite=True)
        optimizer.step()
        norm = cell.norm_report()
        applied = bool(norm["within_profile"])
        if not applied:
            cell.restore_trainable(saved)
            optimizer.load_state_dict(optimizer_before)
        need(other == {n: tensor_hash(named[n]) for n in other}, "PRICE_FACTORS_CHANGED")
        report["steps"].append({
            "attempt": pair["attempt"], "row_ids": pair["row_ids"], "applied": applied,
            "rolled_back": not applied, "action_loss": float(loss.detach()),
            "detached_price_reference_loss": float(price_reference),
            "gradient_norm_before_clip": float(gradient_norm), "gradient_finite": True,
            "norm_before_possible_rollback": norm,
        })
        if pair["attempt"] % 8 == 0:
            print(json.dumps({"attempted": pair["attempt"],
                              "applied": sum(s["applied"] for s in report["steps"])}), flush=True)
    cell.eval()
    cell.assert_frozen()
    need(mask == {n: t.requires_grad for n, t in cell.named_parameters()}, "REQUIRES_GRAD_MASK_CHANGED")
    need(other == {n: tensor_hash(named[n]) for n in other}, "FINAL_PRICE_FACTORS_CHANGED")
    final = cell.trainable_state()
    final_hold_action = replay(holdout, "fixed_final")
    artifact = output / "action32-research-adapter.safetensors"
    need(not artifact.exists(), "RESEARCH_ADAPTER_ALREADY_EXISTS")
    save_file({n: t.cpu().contiguous() for n, t in final.items()}, str(artifact))
    artifact.chmod(0o400)
    labels = torch.tensor([int(not r["parent_correct"]) for r in holdout], dtype=torch.long)

    def metrics(logits, temperature):
        logp = torch.log_softmax(logits.detach().cpu().double() / temperature, -1)
        probabilities = logp.exp()
        prediction = probabilities.argmax(-1)
        return {
            "rows": len(holdout), "class_order": ["ACT", "ESCALATE"],
            "brier": float((probabilities - F.one_hot(labels, 2)).square().sum(-1).mean()),
            "nll": float(-logp[torch.arange(len(labels)), labels].mean()),
            "accuracy": float((prediction == labels).double().mean()),
            "confusion_true_rows_predicted_columns": [
                [int(((labels == truth) & (prediction == choice)).sum()) for choice in (0, 1)]
                for truth in (0, 1)],
            "act_probabilities_all_rows": probabilities[:, 0].tolist(),
        }

    temperature = calibration["action_temperature_training_only"]
    prior = p["evaluation"]["constant_training_population_baseline_act_probability"]
    prior_logits = torch.tensor([[math.log(prior), math.log1p(-prior)]] * len(holdout), dtype=torch.float64)
    report.update(
        state="COMPLETED", attempted_optimizer_steps=len(report["steps"]),
        applied_optimizer_steps=sum(s["applied"] for s in report["steps"]),
        rolled_back_optimizer_steps=sum(s["rolled_back"] for s in report["steps"]),
        gradient_row_presentations=2 * len(report["steps"]),
        changed_action_parameters=sum(int((initial[n] != final[n]).sum()) for n in PARAMETERS),
        final_norm=cell.norm_report(), non_action_gradients_none=True, price_factors_unchanged=True,
        frozen_base_unchanged=True, requires_grad_mask_unchanged=True, parent_price_argmax_parity=True,
        research_adapter_sha256=sha(artifact), holdout_row_ids=[r["row_id"] for r in holdout],
        holdout={
            "frozen_parent_same_train_temperature": metrics(parent_hold_action, temperature),
            "fixed_final_same_train_temperature": metrics(final_hold_action, temperature),
            "constant_training_population_prior": metrics(prior_logits, 1.0),
            "frozen_parent_inference": v2_action_inference(parent_hold_action, calibration),
            "fixed_final_inference": v2_action_inference(final_hold_action, calibration),
        },
        max_gpu_allocated_bytes=torch.cuda.max_memory_allocated(),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--protocol-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    os.umask(0o077)
    need(os.uname().nodename == "alex-G1628-04", "POCKET4_HOST_REQUIRED")
    p, training, holdout = checked_protocol(args.protocol, args.protocol_sha256)
    ready = runtime_ready(p)
    print(json.dumps({"state": ready["state"], "reason": ready["reason"],
                      "training_rows": 32, "training_ACT": 8, "training_ESCALATE": 24,
                      "holdout_rows": 24, "attempted_optimizer_steps": 64,
                      "gradient_row_presentations": 128, "torch_imported": "torch" in sys.modules}), flush=True)
    need("torch" not in sys.modules, "PREFLIGHT_IMPORTED_TORCH")
    if not args.execute:
        return 0
    if ready["state"] != "READY":
        return 2
    need(args.output is not None and args.output.is_absolute()
         and not args.output.exists() and not args.output.is_symlink(), "NEW_ABSOLUTE_OUTPUT_REQUIRED")
    # Existing verification runs config_check in a separate interpreter.
    ready["verifier"].verify_targets(ready["manifest"])
    output = args.output
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    started = time.monotonic()
    report = {
        "schema": "heptatrader.action32-controlled-research-execution.v1",
        "state": "NOT_STARTED", "protocol_path": str(args.protocol),
        "protocol_sha256": args.protocol_sha256, "runner_sha256": sha(Path(__file__)),
        "rollout_verifier_sha256": ROLLOUT_VERIFIER_SHA,
        "created_at_ms": int(time.time() * 1000), "broker_orders_sent": 0,
        "formal_trial_registered": False, "model_promotion": False, "original_trial_changed": False,
    }
    gate = None
    try:
        sys.path.insert(0, ready["guard_import_root"])
        from historical_training import resident_resource_guard as guard
        need(Path(guard.__file__).resolve() == Path(ready["guard_origin"]), "IMPORTED_GUARD_CHANGED")
        config = copy.deepcopy(ready["guard_config"])
        original_lock = Path(config["state_root"]) / "heavy-worker.lock"
        config["state_root"] = str(output / "admission")
        guard.verify_config(config)
        gate = guard.Gate(config, "trainer")
        gate.lease = guard.Lease(original_lock)
        need("torch" not in sys.modules, "TORCH_IMPORTED_BEFORE_ADMISSION")
        if not gate.acquire({"purpose": "FROZEN_ACTION32_CONTROLLED_RESEARCH",
                             "protocol_sha256": args.protocol_sha256}):
            report.update(state="NOT_STARTED_RESOURCE_OR_VALUE_PRIORITY", admission=gate.last_admission)
        else:
            again = runtime_ready(p)
            need(again["state"] == "READY" and again["bindings"] == ready["bindings"],
                 "PAIRED_BINDING_CHANGED_DURING_ADMISSION")
            checked_protocol(args.protocol, args.protocol_sha256)
            write_once(output / "execution.json", {
                **report, "bindings": again["bindings"],
                "rollout_manifest_sha256": again["rollout_manifest_sha256"],
                "qualification_sha256": again["qualification_sha256"],
                "code_root": p["code_root"], "code_files_sha256": p["code_files_sha256"],
                "source_sha256": p["source"]["sha256"], "selection": p["selection"],
                "original_heavy_worker_lock": str(original_lock),
            })
            need(not any(n == "laya_cell" or n.startswith("laya_cell.") for n in sys.modules),
                 "ALGORITHM_ALREADY_IMPORTED_FROM_ANOTHER_RUNTIME")
            sys.path.insert(0, str(Path(p["code_root"]) / "research/neuron_learning"))
            train_model(p, training, holdout, output, report)
    except Exception as exc:
        report.update(state="FAILED", error_type=type(exc).__name__, reason=str(exc))
    finally:
        if gate is not None:
            gate.lease.release()
        report["wall_seconds"] = time.monotonic() - started
        write_once(output / "result.json", report)
    print(json.dumps({"state": report["state"], "output": str(output),
                      "attempted": len(report.get("steps", []))}), flush=True)
    return 0 if report["state"] == "COMPLETED" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"state": "PREFLIGHT_FAILED",
                          "error_type": type(exc).__name__, "reason": str(exc)}), flush=True)
        raise SystemExit(2)
