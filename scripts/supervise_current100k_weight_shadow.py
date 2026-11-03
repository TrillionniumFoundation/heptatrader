#!/usr/bin/env python3
"""Arm frozen research; start its fixed window only when original inputs resume."""
from __future__ import annotations
import argparse, fcntl, hashlib, json, os, signal, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "research"), str(ROOT / "research/neuron_learning")]
from laya_cell import current100k_weight_shadow as W
from laya_cell.current100k_preopen import ResidentIngress
from run_current100k_preopen_research import original_guard
from run_verified_weight_research import verify_release


def pinned_json(path, expected):
    raw = Path(path).read_bytes()
    W.require(hashlib.sha256(raw).hexdigest() == expected, "SUPERVISOR_PIN_CHANGED")
    return json.loads(raw)


def fresh_request(now, requests, maximum_age):
    latest = max((v["request"]["started_at_ms"] for v in requests.values()), default=None)
    return latest, latest is not None and 0 <= now - latest <= maximum_age


def phase(now, plan):
    if now < plan["input_start_ms"]: return "WAIT_FIXED_FUTURE_START"
    if now < plan["input_end_ms"]: return "COLLECTING_FIXED_INPUT_WINDOW"
    if now < plan["outcome_end_ms"]: return "WAIT_FIXED_SETTLEMENT_GRACE"
    return "WAIT_FINAL_ASSESSMENT"


def ensure_activation(config, study, template, root):
    path = root / "plan.json"
    if path.exists():
        W.require((root / "registration.json").exists(), "SUPERVISOR_INCOMPLETE_REGISTRATION")
        plan = W.ShadowStore(root).plan
        for key in ("heads", "producer_registration", "base_id", "training_dataset_sha256", "encoder_backend", "encoder_threads", "encoder_batch_size", "head_execution_backend", "hip_runtime_manifest_sha256"):
            W.require(plan[key] == study[key], "SUPERVISOR_EXISTING_PLAN_DIFFERENT:" + key)
    else:
        plan = W.freeze(study, root)
    predictor = dict(template, study_plan_path=str(path), study_plan_sha256=W.sha(path))
    predictor_path = root / "predictor-config.json"
    W.durable_once(predictor_path, predictor)
    activation_path = root / "activation.json"
    if activation_path.exists():
        activation = W.read(activation_path)
        W.require(activation["plan_id"] == plan["plan_id"] and activation["supervisor_config_sha256"] == config["_sha256"], "SUPERVISOR_ACTIVATION_CHANGED")
    else:
        W.durable_once(activation_path, {"plan_id": plan["plan_id"], "activated_at_ms": W.clock_ms(), "supervisor_config_sha256": config["_sha256"], "study_config_sha256": config["study_config_sha256"], "predictor_config_sha256": W.sha(predictor_path), "broker_orders_sent": 0})
    return plan, predictor_path


def run(config):
    W.require(config.get("schema") == "heptatrader.weight-shadow-supervisor.v1", "SUPERVISOR_SCHEMA")
    W.require(all(config.get(k) is False for k in ("trade_authority", "automatic_selection", "actor_update")), "SUPERVISOR_NO_EFFECT_AUTHORITY")
    release, _ = verify_release(config["release_manifest"], config["release_manifest_sha256"])
    W.require(release == ROOT, "SUPERVISOR_RELEASE_ROOT")
    study = pinned_json(config["study_config_path"], config["study_config_sha256"])
    template = pinned_json(config["predictor_template_path"], config["predictor_template_sha256"])
    qualification = pinned_json(config["qualification_path"], config["qualification_sha256"])
    W.require(qualification.get("backend") == study["encoder_backend"] and qualification.get("tensor_cleanup_verified") is True and qualification["max_request_seconds"] <= qualification["deadline_budget_seconds"], "SUPERVISOR_BACKEND_NOT_QUALIFIED")
    W.require(W.sha(qualification["evidence_path"]) == qualification["evidence_sha256"], "SUPERVISOR_QUALIFICATION_EVIDENCE_CHANGED")
    W.require(template["source_registration"] == study["producer_registration"] and template["period_sha256"] == study["period_sha256"], "SUPERVISOR_SOURCE_SCOPE")
    for key in ("encoder_backend", "encoder_threads", "encoder_batch_size"):
        W.require(template[key] == study[key], "SUPERVISOR_NUMERIC_SCOPE")
    W.require(Path(config["native_stop_marker"]).is_absolute(), "SUPERVISOR_NATIVE_STOP_PATH")
    root = Path(config["state_root"])
    W.require(root.is_absolute() and not root.is_symlink(), "SUPERVISOR_ROOT")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    lock = os.open(root / "supervisor.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stopping = [False]
    def stop(*_): stopping[0] = True
    for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, stop)
    source = ResidentIngress(study["producer_registration"], study["period_sha256"])
    guard = original_guard(template)
    children, retries, last_exits = {}, {}, {}
    launcher = str(ROOT / "scripts/run_verified_weight_research.py")
    def spawn(role, args):
        old = children.get(role)
        if old is not None and old.poll() is not None:
            last_exits[role] = {"code": old.returncode, "observed_at_ms": W.clock_ms()}
            if role == "predictor" and old.returncode < 0:
                guard.invoke("record_native_exit", old.returncode, {"purpose":"CURRENT100K_WEIGHT_SHADOW", "state_root":str(root)})
            children.pop(role)
            retries[role] = time.monotonic() + (5 if old.returncode == 0 else 60)
        if role not in children and time.monotonic() >= retries.get(role, 0):
            command = [config["python"], "-B", launcher, "--manifest", config["release_manifest"], "--sha256", config["release_manifest_sha256"], "--target", role, "--", *args]
            children[role] = subprocess.Popen(command)
    def stop_child(role):
        child = children.pop(role, None)
        if child is not None and child.poll() is None:
            child.terminate()
            try: child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            last_exits[role] = {"code": child.returncode, "observed_at_ms": W.clock_ms()}
    try:
        while not stopping[0]:
            now = W.clock_ms()
            status = {"schema": "heptatrader.weight-shadow-supervisor-status.v1", "at_ms": now, "broker_orders_sent": 0, "automatic_selection": False, "child_exits": dict(last_exits)}
            try:
                pinned_json(config["study_config_path"], config["study_config_sha256"])
                if not (root / "plan.json").exists():
                    _, requests, _, issues = source.read()
                    latest, fresh = fresh_request(now, requests, 60000)
                    status.update(state="ARMED_WAIT_FRESH_ORIGINAL_REQUEST", latest_original_request_at_ms=latest, source_issues=issues)
                    native_stop = os.path.lexists(config["native_stop_marker"])
                    status["native_stop_present"] = native_stop
                    if native_stop: status["state"] = "ARMED_NATIVE_STOP_ACTIVE"
                    if not issues and fresh and not native_stop:
                        admission = guard.check()
                        if admission.get("admit_new_worker") is True: ensure_activation(config, study, template, root)
                        else: status.update(state="ARMED_WAIT_ORIGINAL_SOURCE_AND_RESOURCES", admission_reasons=admission.get("reasons", []))
                if (root / "plan.json").exists():
                    plan, predictor_path = ensure_activation(config, study, template, root)
                    now = W.clock_ms()
                    status.update(state=phase(now, plan), plan_id=plan["plan_id"], input_start_ms=plan["input_start_ms"], input_end_ms=plan["input_end_ms"], outcome_end_ms=plan["outcome_end_ms"])
                    native_stop = os.path.lexists(config["native_stop_marker"])
                    status["native_stop_present"] = native_stop
                    if now < plan["input_end_ms"] and not native_stop: spawn("predictor", ["--config", str(predictor_path), "--sha256", W.sha(predictor_path)])
                    else: stop_child("predictor")
                    spawn("evaluator", ["--root", str(root)])
                    final = root / "assessment.json"
                    if now >= plan["outcome_end_ms"] and final.exists():
                        assessment = W.read(final)
                        if assessment.get("plan_id") == plan["plan_id"] and assessment.get("as_of_ms", 0) >= plan["outcome_end_ms"]:
                            stop_child("evaluator")
                            status.update(state="FINAL_ASSESSMENT_AVAILABLE", assessment_state=assessment["state"], observed_trades=assessment["observed_trades"], final_reasons=assessment["reasons"])
                            W.atomic(root / "supervisor-status.json", status)
                            return status
                status["children"] = {name: {"pid": child.pid, "running": child.poll() is None} for name, child in children.items()}
            except Exception as exc:
                status.update(state="UNAVAILABLE_NO_SELECTION", reason=type(exc).__name__ + ":" + str(exc)[:300])
                stop_child("predictor")
                stop_child("evaluator")
            W.atomic(root / "supervisor-status.json", status)
            for _ in range(10):
                if stopping[0]: break
                time.sleep(1)
    finally:
        stop_child("predictor")
        stop_child("evaluator")
        os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    config = pinned_json(args.config, args.sha256)
    config["_sha256"] = args.sha256
    print(json.dumps(run(config), sort_keys=True), flush=True)

if __name__ == "__main__": main()
