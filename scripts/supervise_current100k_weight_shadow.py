#!/usr/bin/env python3
"""Arm frozen research; start its fixed window only when original inputs resume."""
from __future__ import annotations
import argparse, fcntl, hashlib, json, math, os, signal, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "research"), str(ROOT / "research/neuron_learning")]
from laya_cell import current100k_weight_shadow as W
from laya_cell import current100k_weight_shadow_eval as E
from laya_cell.current100k_preopen import ResidentIngress
from run_current100k_preopen_research import original_guard
from run_verified_weight_research import verify_release

# Processing budget after the frozen outcome deadline, never another data window.
FINAL_REPORT_TIMEOUT_MS = 120000


def terminal_status(root, receipt):
    return {**{key: receipt[key] for key in (
        "plan_id", "state", "checked_at_ms", "outcome_end_ms", "current_replay_complete",
        "observed_trades", "original_command_count", "prediction_coverage", "error",
        "last_completed_assessment", "last_assessment_read_error", "financial_candidate_selected")},
        "terminal_report_path": str(Path(root) / "terminal-unavailable.json"),
        "terminal_report_sha256": W.sha(Path(root) / "terminal-unavailable.json"),
        "final_reasons": receipt["reasons"]}


def fixed_final_status(root, plan, now, stop_evaluator, worker_exits=None):
    """Consume a completed final, or bound how long final publication can fail."""
    if now < plan["outcome_end_ms"]:
        return None
    terminal = E.read_terminal_unavailable(root, plan)
    if terminal is not None:
        return terminal_status(root, terminal)
    final = Path(root) / "assessment.json"
    if final.exists():
        assessment = W.read(final)
        W.require(assessment.get("schema") == "heptatrader.current100k-weight-shadow-assessment.v1" and assessment.get("plan_id") == plan["plan_id"], "SUPERVISOR_FINAL_ASSESSMENT_SCOPE")
        if W.integer(assessment["as_of_ms"]) >= plan["outcome_end_ms"]:
            W.require(assessment["as_of_ms"] <= now and assessment["state"] in ("FINAL_INCOMPLETE", "FINAL_RESEARCH_COMPARISON"), "SUPERVISOR_FINAL_ASSESSMENT_STATE")
            W.require(all(assessment.get(k) is False for k in ("financial_candidate_selected", "canonical_agentd_accepted", "trade_authority")), "SUPERVISOR_FINAL_NO_AUTHORITY")
            return {"state": "FINAL_ASSESSMENT_AVAILABLE", "assessment_state": assessment["state"],
                    "assessment_as_of_ms": assessment["as_of_ms"], "assessment_sha256": W.sha(final),
                    "observed_trades": assessment["observed_trades"], "original_command_count": assessment["original_command_count"],
                    "prediction_coverage": assessment["prediction_coverage"], "final_reasons": assessment["reasons"],
                    "financial_candidate_selected": False}
    publication_deadline = plan["outcome_end_ms"] + FINAL_REPORT_TIMEOUT_MS
    if now < publication_deadline:
        return None
    stop_evaluator()
    terminal = E.publish_final_unavailable(
        root, plan, TimeoutError("FINAL_REPORT_PUBLICATION_TIMEOUT"),
        diagnostics={"publication_deadline_ms": publication_deadline,
                     "publication_timeout_ms": FINAL_REPORT_TIMEOUT_MS,
                     "worker_exits": dict(worker_exits or {}), "data_window_extended": False})
    return terminal_status(root, terminal)


def pinned_json(path, expected):
    raw = Path(path).read_bytes()
    W.require(hashlib.sha256(raw).hexdigest() == expected, "SUPERVISOR_PIN_CHANGED")
    return json.loads(raw)


def fresh_request(now, requests, maximum_age):
    latest = max((v["request"]["started_at_ms"] for v in requests.values()), default=None)
    return latest, latest is not None and 0 <= now - latest <= maximum_age


def successful_decisions(requests, graphs, producer_sha256, behavior_model_id):
    """Verify original RESPONSE/DECISION evidence, including successful HOLDs.

    An opening is deliberately not required: this is source readiness, not
    authority to trade. The same verified attribution graph supplies requests,
    replies and decisions; a recent attempted provider call is insufficient.
    """
    from hepta_decision_attribution import digest, strict
    completed = {}
    for _, producer, originals, responses, decisions, _, invalid, order in graphs:
        if producer != producer_sha256 or invalid:
            continue
        for rid, request in originals.items():
            item = requests.get(rid)
            response, decision = responses.get(rid), decisions.get(rid)
            if not item or not isinstance(response, dict) or not isinstance(decision, dict):
                continue
            try:
                if (item["request"] != request or item["producer_manifest_sha256"] != producer_sha256
                        or request["model_id"] != behavior_model_id
                        or item["request_sha256"] != digest(request)
                        or response["request_id"] != rid or decision["request_id"] != rid
                        or response["request_sha256"] != item["request_sha256"]
                        or decision["request_sha256"] != item["request_sha256"]
                        or decision["response_sha256"] != digest(response)
                        or decision["decision_sha256"] != digest(decision["decision"])
                        or not order["REQUEST"][rid] < order["RESPONSE"][rid] < order["DECISION"][rid]):
                    continue
                stamps = (request["started_at_ms"], response["available_at_ms"], decision["available_at_ms"])
                if any(type(value) is not int or value <= 0 for value in stamps) or not stamps[0] <= stamps[1] <= stamps[2]:
                    continue
                if type(response["returncode"]) is not int or response["returncode"] != 0:
                    continue
                envelope = strict(response["stdout"])
                outputs = envelope.get("outputs")
                if (envelope.get("ok") is not True or not isinstance(outputs, list)
                        or len(outputs) != 1 or not isinstance(outputs[0], dict)
                        or not isinstance(outputs[0].get("text"), str)):
                    continue
                value = strict(outputs[0]["text"])
                if (not isinstance(value, dict) or set(value) != {"action", "confidence", "rationale"}
                        or value["action"] not in ("BUY", "SELL", "HOLD")
                        or type(value["confidence"]) not in (int, float)
                        or not math.isfinite(value["confidence"]) or not 0 <= value["confidence"] <= 1
                        or not isinstance(value["rationale"], str) or value != decision["decision"]):
                    continue
                # The logical model name can be a historical alias. Bind the
                # actual provider/model to the exact original CLI controls.
                provider, model = envelope.get("provider"), envelope.get("model")
                control = request["model_controls"]["model"]
                if (not isinstance(provider, str) or not provider or not isinstance(model, str) or not model
                        or control != provider + "/" + model):
                    continue
                completed[rid] = {
                    "request_id": rid, "request_sha256": item["request_sha256"],
                    "response_sha256": digest(response), "decision_sha256": decision["decision_sha256"],
                    "request_started_at_ms": stamps[0], "response_available_at_ms": stamps[1],
                    "decision_available_at_ms": stamps[2], "action": value["action"],
                    "model_id": behavior_model_id, "provider_model": control,
                }
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                continue
    return completed


class RegistrationSource(ResidentIngress):
    """Reuse verified segment bytes; invalidate completion evidence with ingress."""
    def __init__(self, registration, period_sha256, behavior_model_id):
        super().__init__(registration, period_sha256)
        self.behavior_model_id = behavior_model_id
        self.completion_identity, self.completions = None, {}

    def read_successes(self):
        from hepta_decision_attribution import _verified_producer_graphs
        from laya_cell.financial_forward_source import ingress_identity
        manifest, requests, bindings, issues = self.read()
        if self.completion_identity != self.identity:
            graphs, _ = _verified_producer_graphs(
                manifest["journal_directory"], "EUR.USD", self.period, segment_cache=self.cache)
            W.require(self.identity == ingress_identity(self.registration, self.period),
                      "SUPERVISOR_SOURCE_CHANGED_DURING_RECOVERY_CHECK")
            self.completions = successful_decisions(
                requests, graphs, self.registration["sha256"], self.behavior_model_id)
            self.completion_identity = self.identity
        return manifest, requests, bindings, issues, self.completions


def fresh_successful_decision(now, requests, completed, maximum_age):
    latest_request, _ = fresh_request(now, requests, maximum_age)
    latest = max((value["decision_available_at_ms"] for value in completed.values()), default=None)
    eligible = [value for value in completed.values()
                if value["request_started_at_ms"] == latest_request
                and 0 <= now - value["request_started_at_ms"] <= maximum_age
                and value["request_started_at_ms"] <= value["response_available_at_ms"]
                <= value["decision_available_at_ms"] <= now]
    witness = max(eligible, key=lambda value: (value["decision_available_at_ms"], value["request_id"]), default=None)
    return latest, witness


def phase(now, plan):
    if now < plan["input_start_ms"]: return "WAIT_FIXED_FUTURE_START"
    if now < plan["input_end_ms"]: return "COLLECTING_FIXED_INPUT_WINDOW"
    if now < plan["outcome_end_ms"]: return "WAIT_FIXED_SETTLEMENT_GRACE"
    return "WAIT_FINAL_ASSESSMENT"


def ensure_activation(config, study, template, root, *, source_recovery=None):
    if config.get("continuation") is not None:
        from current100k_weight_continuation import continue_activation
        return continue_activation(config, root)
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
        W.durable_once(activation_path, {"plan_id": plan["plan_id"], "activated_at_ms": W.clock_ms(), "supervisor_config_sha256": config["_sha256"], "study_config_sha256": config["study_config_sha256"], "predictor_config_sha256": W.sha(predictor_path), "broker_orders_sent": 0, **({"source_recovery_witness": source_recovery} if source_recovery is not None else {})})
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
    children, retries, last_exits = {}, {}, {}
    plan = None
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
        child = children.get(role)
        if child is None: return
        if child.poll() is None:
            child.terminate()
            try: child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        W.require(child.poll() is not None, "SUPERVISOR_CHILD_EXIT_UNCONFIRMED")
        last_exits[role] = {"code": child.returncode, "observed_at_ms": W.clock_ms(), "reaped": True}
        children.pop(role)
    status = {"schema": "heptatrader.weight-shadow-supervisor-status.v1",
              "at_ms": W.clock_ms(), "state": "STARTING", "broker_orders_sent": 0,
              "automatic_selection": False, "financial_candidate_selected": False, "supervisor_running": True}
    try:
        source = RegistrationSource(study["producer_registration"], study["period_sha256"], study["behavior_model_id"])
        guard = original_guard(template)
        while not stopping[0]:
            now = W.clock_ms()
            status = {"schema": "heptatrader.weight-shadow-supervisor-status.v1", "at_ms": now, "broker_orders_sent": 0, "automatic_selection": False, "financial_candidate_selected": False, "supervisor_running": True, "child_exits": dict(last_exits)}
            try:
                pinned_json(config["study_config_path"], config["study_config_sha256"])
                if not (root / "plan.json").exists():
                    _, requests, _, issues, completed = source.read_successes()
                    latest, _ = fresh_request(now, requests, 60000)
                    success_at, recovery = fresh_successful_decision(now, requests, completed, 60000)
                    status.update(state="ARMED_WAIT_SUCCESSFUL_ORIGINAL_DECISION",
                                  latest_original_request_at_ms=latest,
                                  latest_successful_original_decision_at_ms=success_at,
                                  source_recovery_witness=recovery, source_issues=issues)
                    native_stop = os.path.lexists(config["native_stop_marker"])
                    status["native_stop_present"] = native_stop
                    if native_stop: status["state"] = "ARMED_NATIVE_STOP_ACTIVE"
                    if not issues and recovery is not None and not native_stop:
                        admission = guard.check()
                        if admission.get("admit_new_worker") is True:
                            # Admission may take time. Revalidate source and age
                            # before allocating an irreversible fixed window.
                            _, requests, _, issues, completed = source.read_successes()
                            latest, _ = fresh_request(W.clock_ms(), requests, 60000)
                            success_at, recovery = fresh_successful_decision(W.clock_ms(), requests, completed, 60000)
                            native_stop = os.path.lexists(config["native_stop_marker"])
                            status.update(latest_original_request_at_ms=latest,
                                          latest_successful_original_decision_at_ms=success_at,
                                          source_recovery_witness=recovery, source_issues=issues,
                                          native_stop_present=native_stop)
                            if native_stop: status["state"] = "ARMED_NATIVE_STOP_ACTIVE"
                            if not issues and recovery is not None and not native_stop:
                                ensure_activation(config, study, template, root, source_recovery=recovery)
                        else: status.update(state="ARMED_WAIT_ORIGINAL_SOURCE_AND_RESOURCES", admission_reasons=admission.get("reasons", []))
                if (root / "plan.json").exists():
                    plan, predictor_path = ensure_activation(config, study, template, root)
                    now = W.clock_ms()
                    status.update(state=phase(now, plan), plan_id=plan["plan_id"], input_start_ms=plan["input_start_ms"], input_end_ms=plan["input_end_ms"], outcome_end_ms=plan["outcome_end_ms"])
                    native_stop = os.path.lexists(config["native_stop_marker"])
                    status["native_stop_present"] = native_stop
                    if now < plan["input_end_ms"] and not native_stop: spawn("predictor", ["--config", str(predictor_path), "--sha256", W.sha(predictor_path)])
                    else: stop_child("predictor")
                    final = fixed_final_status(root, plan, now, lambda: stop_child("evaluator"), last_exits)
                    if final is not None:
                        stop_child("evaluator")
                        status.update(final)
                        status.update(at_ms=W.clock_ms(), child_exits=dict(last_exits))
                        W.atomic(root / "supervisor-status.json", status)
                        return status
                    spawn("evaluator", ["--root", str(root)])
                status["children"] = {name: {"pid": child.pid, "running": child.poll() is None} for name, child in children.items()}
            except Exception as exc:
                status.update(state="UNAVAILABLE_NO_SELECTION", reason=type(exc).__name__ + ":" + str(exc)[:300])
                stop_child("predictor")
                stop_child("evaluator")
                # A source/config failure after the fixed end must not keep this
                # supervisor alive forever or turn an old assessment into a final.
                if plan is None and (root / "plan.json").exists():
                    try: plan = W.ShadowStore(root).plan
                    except Exception as plan_exc:
                        status["registered_plan_read_error"] = type(plan_exc).__name__ + ":" + str(plan_exc)[:300]
                checked = W.clock_ms()
                if plan is not None and checked >= plan["outcome_end_ms"]:
                    try:
                        terminal = E.publish_final_unavailable(root, plan, exc, checked_at_ms=checked,
                                                               diagnostics={"worker_exits": dict(last_exits)})
                        status.update(terminal_status(root, terminal))
                    except Exception as final_exc:
                        status.update(state="FINAL_UNAVAILABLE_NO_SELECTION", plan_id=plan["plan_id"], checked_at_ms=checked,
                                      outcome_end_ms=plan["outcome_end_ms"], current_replay_complete=False,
                                      observed_trades=None, original_command_count=None, prediction_coverage=None,
                                      error={"type": type(exc).__name__, "message": str(exc)[:2000]},
                                      terminal_report_error={"type": type(final_exc).__name__, "message": str(final_exc)[:2000]})
                    status.update(at_ms=W.clock_ms(), child_exits=dict(last_exits))
                    W.atomic(root / "supervisor-status.json", status)
                    return status
            W.atomic(root / "supervisor-status.json", status)
            for _ in range(10):
                if stopping[0]: break
                time.sleep(1)
    finally:
        failure = sys.exc_info()[1]
        cleanup_errors = []
        # Attempt both cleanups even if one child cannot be stopped. Retain an
        # unconfirmed child's actual state instead of reporting a false exit.
        for role in ("predictor", "evaluator"):
            try: stop_child(role)
            except Exception as exc:
                cleanup_errors.append((role, exc))
        stopped_at = W.clock_ms()
        status.update(at_ms=stopped_at, stopped_at_ms=stopped_at, supervisor_running=False,
                      child_exits=dict(last_exits),
                      children={name: {"pid": child.pid, "running": child.poll() is None}
                                for name, child in children.items()})
        if failure is not None or cleanup_errors:
            error = failure if failure is not None else cleanup_errors[0][1]
            status.update(state="FAILED", reason=type(error).__name__ + ":" + str(error)[:300])
            if cleanup_errors:
                status["cleanup_errors"] = [{"role": role, "reason": type(exc).__name__ + ":" + str(exc)[:300]}
                                            for role, exc in cleanup_errors]
        elif status.get("state") not in ("FINAL_ASSESSMENT_AVAILABLE", "FINAL_UNAVAILABLE_NO_SELECTION"):
            status.update(state="STOPPED", stop_reason="SUPERVISOR_STOP_REQUESTED")
        try:
            W.atomic(root / "supervisor-status.json", status)
        finally:
            os.close(lock)
        if failure is None and cleanup_errors:
            raise cleanup_errors[0][1]
    return status


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
