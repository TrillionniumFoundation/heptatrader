#!/usr/bin/env python3
"""Read-only progress from selected systemd runtimes; never grants trade authority."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import time

SCHEMA = "heptatrader.progress.v1"
CORE_UNITS = (
    "heptatrader-execution.service", "heptatrader-gateway.service",
    "heptatrader-paper-driver.service", "heptatrader-crypto-driver.service",
)
PATTERNS = ("heptatrader-laya-*", "heptatrader-current100k-*", "heptatrader-value-*")
MAX_JSON_BYTES = 4 * 1024 * 1024


def _object(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("STATUS_NOT_REGULAR")
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("STATUS_TOO_LARGE")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("STATUS_NOT_OBJECT")
    return value


def freshness(at_ms, now_ms, max_age_ms):
    if type(at_ms) is not int or at_ms <= 0:
        return {"at_ms": None, "age_ms": None, "freshness": "UNKNOWN"}
    age = now_ms - at_ms
    return {"at_ms": at_ms, "age_ms": age,
            "freshness": "FUTURE" if age < 0 else "FRESH" if age <= max_age_ms else "STALE"}


def observe(path, now_ms, max_age_ms, *, timestamps=("at_ms",)):
    meta = {"source": str(path), "available": False}
    try:
        value = _object(path)
    except (OSError, ValueError, TypeError) as exc:
        return {}, {**meta, "error": type(exc).__name__ + ":" + str(exc),
                    **freshness(None, now_ms, max_age_ms)}
    at = next((value[k] for k in timestamps if type(value.get(k)) is int), None)
    return value, {**meta, "available": True, **freshness(at, now_ms, max_age_ms)}


def presence(path):
    try:
        Path(path).lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return None


def pick(value, *keys):
    return {key: value[key] for key in keys if key in value}


def argument(argv, flag):
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def exec_argv(text):
    if "argv[]=" not in text:
        return []
    raw = text.split("argv[]=", 1)[1].split(" ; ", 1)[0]
    raw = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: "\\\\x" + m[1], raw)
    try:
        return [re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m[1], 16)), token)
                for token in shlex.split(raw)]
    except ValueError:
        return []


def discover_units():
    def run(arguments):
        result = subprocess.run(["systemctl", "--user", "--no-pager", *arguments],
                                text=True, capture_output=True, timeout=5)
        if result.returncode:
            raise RuntimeError("SYSTEMD_READ_FAILED:" + result.stderr.strip()[:240])
        return result.stdout
    names = set(CORE_UNITS)
    for line in run(["list-unit-files", "--no-legend", *PATTERNS]).splitlines():
        fields = line.split()
        if fields and re.fullmatch(r"heptatrader-[a-zA-Z0-9_.@-]+\.(service|timer)", fields[0]):
            names.add(fields[0])
    properties = "Id,LoadState,ActiveState,SubState,Result,MainPID,UnitFileState,ExecStart"
    output = run(["show", "--property=" + properties, *sorted(names)])
    units = {}
    for block in output.strip().split("\n\n"):
        item = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if "Id" not in item:
            continue
        configured = exec_argv(item.get("ExecStart", ""))
        item["_argv"] = configured
        item["configured_config_path"] = argument(configured, "--config")
        item["running_config_path"] = None
        item["config_source"] = "CONFIGURED_NEXT_INVOCATION"
        item["configuration_matches_process"] = None
        pid = int(item.get("MainPID", "0") or "0")
        if pid:
            try:
                raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
                if not raw or len(raw) > 131072:
                    raise ValueError("PROCESS_ARGV_UNAVAILABLE")
                actual = [part.decode("utf-8") for part in raw.rstrip(b"\0").split(b"\0")]
                item["_argv"] = actual
                item["running_config_path"] = argument(actual, "--config")
                item["config_source"] = "RUNNING_PROCESS_ARGV"
                item["configuration_matches_process"] = actual == configured
            except (OSError, UnicodeError, ValueError) as exc:
                item["config_source"] = "CONFIGURED_PROCESS_UNATTESTED"
                item["process_read_error"] = type(exc).__name__
        units[item["Id"]] = item
    return units


def unit_view(item):
    return pick(item, "Id", "LoadState", "ActiveState", "SubState", "Result",
                "MainPID", "UnitFileState", "config_source",
                "configured_config_path", "running_config_path",
                "configuration_matches_process", "process_read_error")


def selected_unit(name, item, units):
    if item.get("ActiveState") in ("active", "activating", "reloading"):
        return True
    timer = units.get(name.removesuffix(".service") + ".timer", {})
    return (item.get("UnitFileState") in ("enabled", "enabled-runtime")
            or timer.get("ActiveState") in ("active", "activating")
            or timer.get("UnitFileState") in ("enabled", "enabled-runtime"))


def _child(root, identity):
    if (not isinstance(identity, str) or identity in ("", ".", "..")
            or re.fullmatch(r"[A-Za-z0-9_.-]+", identity) is None):
        raise ValueError("INVALID_CURRENT_JOB_ID")
    return Path(root) / identity


def candidate_progress(config, status, now_ms, max_age_ms):
    registration = status.get("latest_candidate_registration") or {}
    result = {"registration": registration, "price": {}, "action": {},
              "latest_forward": {"available": False}}
    job = registration.get("job_id")
    if not job:
        result["evidence_error"] = "LATEST_JOB_BINDING_UNAVAILABLE"
        return result
    artifact = _child(config["candidate_directory"], job) / "artifact" / "candidate.json"
    candidate, meta = observe(artifact, now_ms, max_age_ms, timestamps=("created_at_ms",))
    result["candidate_evidence"] = meta
    expected = status.get("last_candidate_id") or registration.get("candidate_id")
    if not meta["available"] or not expected or candidate.get("candidate_id") != expected:
        result["evidence_error"] = "LATEST_CANDIDATE_UNAVAILABLE_OR_IDENTITY_MISMATCH"
        return result
    diagnostics = candidate.get("training_diagnostics") or {}
    steps = diagnostics.get("optimizer_steps") or {}
    result["price"] = {
        "candidate_id": candidate["candidate_id"], "optimizer_steps": steps,
        "actual_update_proven": type(steps.get("applied")) is int and steps["applied"] > 0,
        "training_examples": diagnostics.get("training_examples"),
        "training_metrics": diagnostics.get("training_metrics"),
        "measurement": candidate.get("measurement"),
        "row_accounting": candidate.get("row_accounting"),
        "training_lineage": candidate.get("training_lineage", status.get("training_lineage")),
        "training_episode": candidate.get("training_episode"),
        "learning_curve": candidate.get("learning_curve"),
        "continuous_episode": diagnostics.get("continuous_episode"),
        "training_only": True, "future_value_proven": False,
    }
    result["action"] = {
        "head_trained": candidate.get("action_head_trained"),
        "optimizer_admitted": candidate.get("action_optimizer_admitted"),
        "optimizer_steps_applied": candidate.get("action_update_steps",
                                                  diagnostics.get("action_optimizer_steps_applied")),
        "support": diagnostics.get("action_support_diagnostic"),
    }
    folder = _child(config["evaluation_directory"], job)
    terminal = folder / "qualification.json"
    path = terminal if terminal.exists() else folder / "progress.json"
    forward, evidence = observe(path, now_ms, max_age_ms, timestamps=("as_of_ms", "at_ms"))
    if (evidence["available"] and
            (forward.get("candidate_id") != expected or
             (registration.get("plan_id") and forward.get("plan_id") != registration["plan_id"]))):
        result["latest_forward"] = {**evidence, "available": False, "error": "FORWARD_IDENTITY_MISMATCH"}
    else:
        result["latest_forward"] = {**evidence, **pick(forward, "state", "reason", "results",
            "observed", "censored", "same_window_price_baselines", "promotion_performed",
            "canonical_owner_admitted", "future_value_proven")}
    return result


def learning_progress(name, item, config, original_path, wrapper, role, now_ms, max_age_ms):
    directory = config["control_directory" if role == "trainer" else "evaluation_directory"]
    status, meta = observe(Path(directory) / "status.json", now_ms, max_age_ms)
    result = {"unit": name, "role": role, "config_path": str(original_path),
              "objective": config.get("objective"), "runtime_directory": config.get("runtime_directory"),
              "service": unit_view(item),
              "status": {**meta, **pick(status, "state", "reason", "job_counts", "active_job",
                  "last_candidate_id", "training_lineage", "counts", "plans", "observed", "censored",
                  "counts_include_closed_plans", "active_prediction_workers",
                  "worker_failure_details", "automatic_trading", "canonical_owner_connected")}}
    if role == "trainer" and meta["available"]:
        result.update(candidate_progress(config, status, now_ms, max_age_ms))
    if wrapper.get("state_root"):
        root = Path(wrapper["state_root"])
        admission, info = observe(root / (role + "-admission.json"), now_ms, max_age_ms)
        result["resource_admission"] = {**info, **pick(admission, "admit_new_worker", "reasons",
            "resource_role", "minimum_available_bytes", "memory_bytes", "full_memory_psi")}
        error, info = observe(root / (role + "-error.json"), now_ms, max_age_ms)
        result["guard_error"] = {**info, **pick(error, "error", "active", "resolved_at_ms"),
            "current_failure": error.get("active") is True and info["freshness"] == "FRESH"}
    return result


def inventory_progress(config, now_ms, max_age_ms):
    value, meta = observe(Path(config["output_root"]) / "status.json", now_ms, max_age_ms,
                          timestamps=("input_checked_at_ms", "at_ms"))
    incremental = value.get("incremental") or {}
    if not isinstance(incremental, dict):
        incremental = {}
    growth = freshness(value.get("last_inventory_growth_at_ms"), now_ms, max_age_ms)
    return {**meta, **pick(value, "state", "reason", "row_count", "bound_opens",
        "observed_original_requests", "input_checked_at_ms", "last_full_evaluation_at_ms",
        "evaluation_skipped", "last_inventory_growth_at_ms", "model_promoted",
        "automatic_training"), "incremental": incremental,
        "new_episodes_this_check": 0 if value.get("evaluation_skipped") is True
                                  else incremental.get("new_episodes"),
        "last_inventory_growth_age_ms": growth["age_ms"], "inventory_is_efficacy": False}


def timing_progress(path, now_ms, max_age_ms):
    value, meta = observe(path, now_ms, max_age_ms, timestamps=("captured_at_ms", "at_ms"))
    return {**meta, **pick(value, "open_coverage", "not_preopen_reason_counts", "groups",
        "predictor_signatures_verified", "evaluator_signatures_verified",
        "signed_outcome_set_verified", "trade_authority")}


def trading_progress(home, runtime, units, now_ms, max_age_ms):
    root = Path(home) / ".local/state/heptatrader"
    blockers = []
    unknown = []
    services = {name: unit_view(units.get(name, {})) for name in CORE_UNITS}
    for name in CORE_UNITS:
        state = units.get(name, {}).get("ActiveState")
        if state and state != "active":
            blockers.append({"reason": "SERVICE_NOT_ACTIVE", "unit": name, "state": state})
        elif not state:
            unknown.append("UNIT_STATE_UNAVAILABLE:" + name)
    switch = Path(runtime) / "control/kill-switch"
    marker_present = presence(switch)
    if marker_present is None:
        unknown.append("NATIVE_KILL_SWITCH_UNREADABLE")
    if marker_present:
        blockers.append({"reason": "NATIVE_KILL_SWITCH_PRESENT", "source": str(switch)})
    hold_path = root / "operator-maintenance/maintenance-hold.json"
    hold, hold_meta = observe(hold_path, now_ms, max_age_ms, timestamps=("created_at_ms",))
    hold_present = presence(hold_path)
    if hold_present is None:
        unknown.append("PERSISTENT_MAINTENANCE_HOLD_UNREADABLE")
    if hold_present:
        blockers.append({"reason": "PERSISTENT_MAINTENANCE_HOLD" if hold_meta["available"]
                         else "PERSISTENT_MAINTENANCE_HOLD_UNREADABLE",
                         "source": str(hold_path), "description": hold.get("reason")})
    drivers = {}
    for lane in ("paper", "crypto"):
        path = root / (lane + "-driver") / "state.json"
        data, meta = observe(path, now_ms, max_age_ms)
        loop = data.get("resident_loop_progress") or {}
        if not isinstance(loop, dict):
            loop = {}
        heartbeat = freshness(loop.get("at_ms"), now_ms, max_age_ms)
        main_pid = int(units.get("heptatrader-" + lane + "-driver.service", {}).get("MainPID", "0") or "0")
        process_match = loop.get("pid") == main_pid if main_pid and type(loop.get("pid")) is int else None
        current_loop = heartbeat["freshness"] == "FRESH" and process_match is True
        item = {"source": str(path), "available": meta["available"], "heartbeat": heartbeat,
                "process_matches_heartbeat": process_match,
                "current_pauses": [], "stale_pauses": [], "historical_last_error": data.get("last_error"),
                "historical_last_error_at_ms": data.get("last_error_at_ms"),
                "last_error_is_current_fault": False,
                **pick(data, "halt_new_entries", "lease_uncertain")}
        if not meta["available"]:
            item["error"] = meta.get("error")
        if not current_loop:
            unknown.append("DRIVER_HEARTBEAT_NOT_CURRENT_PROCESS:" + lane)
        for field in ("entry_inference_pause", "model_inference_pause"):
            pause = data.get(field)
            if not isinstance(pause, dict) or not pause.get("reason_code"):
                continue
            check = freshness(pause.get("checked_at_ms"), now_ms, max_age_ms)
            record = {"field": field, **pause, **check}
            if check["freshness"] == "FRESH" and current_loop:
                item["current_pauses"].append(record)
                blockers.append({"reason": pause["reason_code"], "driver": lane, "field": field})
            else:
                item["stale_pauses"].append(record)
        if current_loop:
            for field in ("halt_new_entries", "lease_uncertain"):
                if data.get(field) is True:
                    blockers.append({"reason": field.upper(), "driver": lane})
        drivers[lane] = item
    return {"permission": "BLOCKED" if blockers else "NATIVE_ADMISSION_NOT_ATTESTED",
            "current_blockers": blockers, "unknown_current_evidence": unknown,
            "native_kill_switch_present": marker_present,
            "maintenance_hold": {**hold_meta, **pick(hold, "reason", "task", "resume_when")},
            "drivers": drivers, "services": services, "trade_authority_granted": False}


def collect(home, runtime, units, now_ms, max_age_ms=300000, timing_reports=()):
    result = {"schema": SCHEMA, "observed_at_ms": now_ms, "read_only": True,
              "learning": [], "value_inventory": [], "value_forward": [],
              "value_prediction": [], "value_timing": [], "source_errors": []}
    rollout_path = Path(home) / ".local/state/heptatrader/operator-maintenance/training-episode-rollout/status.json"
    rollout, rollout_meta = observe(rollout_path, now_ms, max_age_ms)
    result["training_rollout"] = {**rollout_meta, **pick(rollout, "state", "reason",
        "waiting_for_plan_id", "target_objective", "target_runtime", "expected_outcome_end_ms",
        "trainer_paused", "committed_at_ms")}
    awaiting_rollout = rollout.get("state") == "WAIT_FROZEN_TRIAL_43"
    timing_paths = {str(Path(path)) for path in timing_reports}
    for name, item in sorted(units.items()):
        selected = selected_unit(name, item, units)
        if not name.endswith(".service") or not (selected or (
                awaiting_rollout and name == "heptatrader-laya-trainer.service")):
            continue
        argv = item.get("_argv", [])
        module = argument(argv, "-m") or ""
        config_path = argument(argv, "--config")
        if not config_path:
            continue
        try:
            config = _object(config_path)
            if module == "historical_training.resident_resource_guard":
                original_path = config["original_config"]
                original = _object(original_path)
                role = argument(argv, "--role")
                if role not in ("trainer", "evaluator"):
                    raise ValueError("UNKNOWN_RESIDENT_ROLE")
                result["learning"].append(learning_progress(
                    name, item, original, original_path, config, role, now_ms, max_age_ms))
            elif module in ("laya_cell.resident", "laya_cell.resident_evaluate"):
                role = "trainer" if module == "laya_cell.resident" else "evaluator"
                result["learning"].append(learning_progress(
                    name, item, config, config_path, {}, role, now_ms, max_age_ms))
            elif module == "historical_training.consumer_launch":
                result["value_inventory"].append({"unit": name, "service": unit_view(item),
                    "config_path": config_path, **inventory_progress(config, now_ms, max_age_ms)})
            elif module == "historical_training.value_successor_evaluator":
                root = argument(argv, "--output-root")
                if not root:
                    raise ValueError("SUCCESSOR_OUTPUT_ROOT_UNAVAILABLE")
                value, meta = observe(Path(root) / "successor-status.json", now_ms, max_age_ms)
                result["value_forward"].append({"unit": name, "service": unit_view(item),
                    "config_path": config_path, **meta, **pick(value, "state", "observed_trades",
                    "total_original_trades", "unresolved_or_censored", "evaluation_skipped",
                    "assessment_as_of_ms", "next_evaluation_due_ms", "skip_reason",
                    "canonical_agentd_accepted", "automatic_selection")})
            elif (module == "laya_cell.financial_forward_evaluate" or
                  module == "laya_cell.current100k_forward_worker" and argument(argv, "--mode") == "evaluate"):
                root = Path(config["forward_root"])
                value, meta = observe(root / "evaluator-status.json", now_ms, max_age_ms)
                result["value_forward"].append({"unit": name, "service": unit_view(item),
                    "config_path": config_path, **meta, **pick(value, "state", "reason", "plans",
                    "states", "observed_trades", "original_command_count", "plan_id",
                    "input_start_ms", "input_end_ms", "canonical_agentd_accepted",
                    "financial_candidate_selected", "actor_updated", "broker_orders_sent")})
            elif (module == "laya_cell.financial_forward_predict" or
                  module == "laya_cell.current100k_forward_worker" and argument(argv, "--mode") == "predict"):
                root = Path(config["forward_root"])
                value, meta = observe(root / "predictor-status.json", now_ms, max_age_ms)
                result["value_prediction"].append({"unit": name, "service": unit_view(item),
                    "config_path": config_path, **meta, **pick(value, "state", "reason",
                    "current_issues", "current_prediction_failures", "historical_failed_or_missed_requests", "front_timing",
                    "publication_timings_ms", "new_predictions", "sealed_predictions", "plans")})
                timing_paths.add(str(root / "value-timing-status.json"))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            result["source_errors"].append({"unit": name, "config_path": config_path,
                                           "error": type(exc).__name__ + ":" + str(exc)})
    result["value_timing"] = [timing_progress(path, now_ms, max_age_ms)
                              for path in sorted(timing_paths)]
    result["trading"] = trading_progress(home, runtime, units, now_ms, max_age_ms)
    return result


def format_text(value):
    lines = ["HeptaTrader progress (read-only; training, evidence and permission are separate)"]
    def show(item):
        return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    rollout = value.get("training_rollout", {})
    if rollout.get("available"):
        lines.append("TRAINING rollout " + show(pick(rollout, "state", "reason", "waiting_for_plan_id",
            "target_objective", "target_runtime", "expected_outcome_end_ms", "trainer_paused", "committed_at_ms")))
    for entry in value["learning"]:
        status = entry["status"]
        lines.append("LEARNING " + entry["role"] + " " + show(pick(status,
            "state", "reason", "freshness", "age_ms", "job_counts", "counts", "observed", "censored")))
        lines.append("RUNTIME " + show({"unit": entry["unit"],
            "objective_schema": (entry.get("objective") or {}).get("schema"),
            **pick(entry["service"], "ActiveState", "SubState", "Result", "config_source",
                   "configured_config_path", "running_config_path", "configuration_matches_process")}))
        if "price" in entry:
            price = entry["price"]
            lines.append("PRICE " + show(pick(price, "candidate_id", "optimizer_steps",
                "actual_update_proven", "training_metrics", "training_lineage")))
            curve = price.get("learning_curve")
            if curve:
                lines.append("PRICE learning_curve " + show(pick(curve,
                    "training_only", "optimizer_reset_between_epochs", "checkpoints")))
            lines.append("ACTION " + show(entry["action"]))
            lines.append("FORWARD " + show(entry["latest_forward"]))
        admission = entry.get("resource_admission", {})
        if admission.get("admit_new_worker") is False:
            lines.append("ADMISSION " + show(pick(admission, "freshness", "age_ms", "reasons")))
        guard_error = entry.get("guard_error", {})
        if guard_error.get("error") and guard_error.get("available"):
            label = "GUARD current" if guard_error.get("current_failure") else "GUARD historical_or_unconfirmed"
            lines.append(label + " " + show(pick(guard_error, "error", "freshness", "active", "resolved_at_ms")))
    for entry in value["value_inventory"]:
        lines.append("VALUE inventory " + show(pick(entry, "state", "service", "freshness", "age_ms",
            "row_count", "new_episodes_this_check", "last_inventory_growth_age_ms",
            "input_checked_at_ms", "last_full_evaluation_at_ms", "evaluation_skipped",
            "inventory_is_efficacy")))
    for entry in value["value_forward"]:
        lines.append("VALUE forward " + show(pick(entry, "unit", "service", "state", "reason", "freshness",
            "plans", "states", "observed_trades", "original_command_count", "total_original_trades",
            "assessment_as_of_ms", "evaluation_skipped")))
    for entry in value["value_prediction"]:
        lines.append("VALUE prediction " + show(pick(entry, "unit", "service", "state", "freshness",
            "current_issues", "current_prediction_failures", "historical_failed_or_missed_requests", "front_timing")))
    for entry in value["value_timing"]:
        lines.append("VALUE OPEN timing " + show(pick(entry, "source", "freshness", "available",
            "open_coverage", "not_preopen_reason_counts", "groups", "error")))
    trade = value["trading"]
    lines.append("TRADING " + show(pick(trade, "permission", "current_blockers", "unknown_current_evidence")))
    history = {lane: item["historical_last_error"] for lane, item in trade["drivers"].items()
               if item["historical_last_error"]}
    if history:
        lines.append("HISTORICAL driver last_error (not a current fault) " + show(history))
    for error in value["source_errors"]:
        lines.append("SOURCE unavailable " + show(error))
    if not any(value[key] for key in ("learning", "value_inventory", "value_forward", "value_prediction")):
        lines.append("RESEARCH status unavailable: no selected systemd runtime was discovered.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--max-age-seconds", type=int, default=300)
    parser.add_argument("--value-timing-report", action="append", default=[], type=Path,
                        help="explicit read-only timing capture; may be repeated")
    args = parser.parse_args(argv)
    if args.max_age_seconds < 1:
        parser.error("--max-age-seconds must be positive")
    now = int(time.time() * 1000)
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/" + str(os.getuid()))) / "heptatrader"
    try:
        units = discover_units()
        discovery_error = None
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
        units = {}
        discovery_error = str(exc)
    result = collect(Path.home(), runtime, units, now, args.max_age_seconds * 1000,
                     args.value_timing_report)
    if discovery_error:
        result["source_errors"].append({"unit": "systemd", "error": discovery_error})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True) if args.format == "json"
          else format_text(result))
    return 0 if not discovery_error else 1


if __name__ == "__main__":
    raise SystemExit(main())
