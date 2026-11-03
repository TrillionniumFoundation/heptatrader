"""Consume successful original features for a separately registered head family.

This observer owns only the new RequestLedger and CPU heads. It never acquires
an encoder lease, loads an encoder, changes the source study, or borrows the
source publication's clock. The original guarded runner owns the later handoff.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import re
import signal
import time
import uuid

from hepta_decision_attribution import read_private, strict
from . import current100k_weight_shadow as W
from .current100k_preopen import (
    RequestLedger, ResidentIngress, SourceSnapshotBusy, load, publish_once,
    require, wall_ms,
)


SOURCE_SCHEMA = "heptatrader.current100k-shared-feature-source.v1"
SOURCE_FILES = {
    "plan": "plan.json", "registration": "registration.json",
    "activation": "activation.json", "predictor_config": "predictor-config.json",
}
MAX_JSON_BYTES = 16 * 1024 * 1024
NUMERIC_FIELDS = (
    "base_id", "behavior_model_id", "period_sha256", "producer_registration",
    "feature_profile", "numeric_profile", "encoder_backend", "encoder_threads",
    "encoder_batch_size", "hip_runtime_manifest_sha256",
)


def _captured(path, expected=None):
    path = Path(path)
    require(path.is_absolute(), "SHARED_ABSOLUTE_PATH")
    raw = read_private(path, MAX_JSON_BYTES)
    sha256 = hashlib.sha256(raw).hexdigest()
    if expected is not None:
        require(sha256 == W.hash_value(expected), "SHARED_PIN_CHANGED:" + str(path))
    return strict(raw), sha256


def protocol_for(config, plan):
    """Exactly the original runner's protocol, so handoff reopens the same lock."""
    value = {key: copy.deepcopy(plan[key]) for key in (
        "plan_id", "input_start_ms", "input_end_ms", "period_sha256",
        "producer_registration",
    )}
    value.update(
        max_request_age_ms=config.get("max_request_age_ms", 60000),
        max_requests=config.get("max_requests", 10000),
        encoder_backend=config["encoder_backend"],
        encoder_threads=config["encoder_threads"],
        encoder_batch_size=config["encoder_batch_size"],
        cpu_affinity=copy.deepcopy(config["cpu_affinity"]),
        study_plan_sha256=config["study_plan_sha256"],
    )
    return value


class SharedFeatureSource:
    """Read immutable successes without locking or writing the source ledger."""

    def __init__(self, config, target_store):
        reference = config.get("shared_feature_source")
        require(isinstance(reference, dict) and set(reference) == {"schema", *SOURCE_FILES}
                and reference["schema"] == SOURCE_SCHEMA, "SHARED_SOURCE_CONFIG")
        self.reference = copy.deepcopy(reference)
        documents = {}
        for name in SOURCE_FILES:
            ref = reference[name]
            require(isinstance(ref, dict) and set(ref) == {"path", "sha256"},
                    "SHARED_SOURCE_PIN_FIELDS")
            documents[name], _ = _captured(ref["path"], ref["sha256"])
        self.study_root = Path(reference["plan"]["path"]).parent
        for name, filename in SOURCE_FILES.items():
            require(Path(reference[name]["path"]) == self.study_root / filename,
                    "SHARED_ORIGINAL_SOURCE_FILE:" + name)
        self.store = W.ShadowStore(self.study_root)
        self.plan = self.store.plan
        require(self.plan == documents["plan"], "SHARED_SOURCE_PLAN_CHANGED")
        self.config = documents["predictor_config"]
        activation = documents["activation"]
        require(activation["plan_id"] == self.plan["plan_id"]
                and activation["predictor_config_sha256"] == reference["predictor_config"]["sha256"],
                "SHARED_SOURCE_ACTIVATION")
        require(self.config.get("schema") == "heptatrader.current100k-preopen-research-config.v1"
                and "shared_feature_source" not in self.config,
                "SHARED_SOURCE_MUST_BE_ORIGINAL_ENCODER")
        require(Path(self.config["study_plan_path"]) == self.study_root / "plan.json"
                and self.config["study_plan_sha256"] == reference["plan"]["sha256"],
                "SHARED_SOURCE_PREDICTOR_PLAN")
        self.ledger_root = Path(self.config["state_root"])
        require(self.ledger_root.is_absolute() and not self.ledger_root.is_symlink(),
                "SHARED_SOURCE_LEDGER_ROOT")

        target = target_store.plan
        require(target["plan_id"] != self.plan["plan_id"]
                and self.plan["registered_at_ms"] < target["registered_at_ms"]
                and self.plan["input_start_ms"] < target["input_start_ms"]
                and self.plan["input_end_ms"] < target["input_end_ms"],
                "SHARED_REQUIRES_NEW_REGISTERED_FAMILY")
        for key in NUMERIC_FIELDS:
            require(self.plan.get(key) == target.get(key), "SHARED_NUMERIC_OR_SOURCE_SCOPE:" + key)
        require(target.get("head_execution_backend", "TORCH_CPU_FP32") == "TORCH_CPU_FP32"
                and self.plan.get("head_execution_backend", "TORCH_CPU_FP32") == "TORCH_CPU_FP32",
                "SHARED_CPU_HEADS_REQUIRED")
        for cfg, plan in ((self.config, self.plan), (config, target)):
            require(all(cfg.get(key) is False for key in (
                "trade_authority", "automatic_selection", "actor_update")), "SHARED_NO_EFFECT_AUTHORITY")
            require(cfg["source_registration"] == plan["producer_registration"]
                    and cfg["period_sha256"] == plan["period_sha256"], "SHARED_CONFIG_PRODUCER_SCOPE")
            for key in ("encoder_backend", "encoder_threads", "encoder_batch_size"):
                require(cfg[key] == plan[key], "SHARED_CONFIG_NUMERIC_SCOPE:" + key)
        helper = "research/paired_net_100k_preflight/core.py"
        require(helper in self.config["encoder_runtime_files_sha256"]
                and self.config["encoder_runtime_files_sha256"][helper]
                == config["encoder_runtime_files_sha256"].get(helper), "SHARED_FEATURE_TEXT_CONTRACT")
        require(self.config["encoder_runtime_files_sha256"] == config["encoder_runtime_files_sha256"],
                "SHARED_ENCODER_RUNTIME_CONTRACT")

        target_root = target_store.root
        require(Path(config["study_plan_path"]) == target_root / "plan.json"
                and config["study_plan_sha256"] == W.sha(target_root / "plan.json"),
                "SHARED_TARGET_PLAN_PIN")
        self.target_pins = {}
        for filename in SOURCE_FILES.values():
            value, sha256 = _captured(target_root / filename)
            self.target_pins[str(target_root / filename)] = sha256
            if filename == "activation.json":
                target_activation = value
            elif filename == "predictor-config.json":
                require(value == config, "SHARED_TARGET_ACTIVATED_CONFIG_CHANGED")
        require(target_activation["plan_id"] == target["plan_id"]
                and target_activation["predictor_config_sha256"]
                == self.target_pins[str(target_root / "predictor-config.json")]
                and target["registered_at_ms"] <= W.integer(target_activation["activated_at_ms"])
                < target["input_start_ms"], "SHARED_TARGET_ACTIVATION_MUST_PRECEDE_WINDOW")
        source_roots = (self.study_root.resolve(), self.ledger_root.resolve())
        for destination in (target_root.resolve(), Path(config["state_root"]).resolve()):
            require(all(not destination.is_relative_to(old) and not old.is_relative_to(destination)
                        for old in source_roots), "SHARED_SOURCE_AND_TARGET_STORAGE_OVERLAP")
        self.protocol = protocol_for(self.config, self.plan)
        self.target_plan_id = target["plan_id"]

    def check_pins(self):
        for name in SOURCE_FILES:
            ref = self.reference[name]
            _captured(ref["path"], ref["sha256"])
        for path, sha256 in self.target_pins.items():
            _captured(path, sha256)

    def binding(self):
        return {
            "schema": "heptatrader.current100k-shared-feature-binding.v1",
            "plan_id": self.target_plan_id, "source_plan_id": self.plan["plan_id"],
            "source": copy.deepcopy(self.reference), "source_input_end_ms": self.plan["input_end_ms"],
            "handoff": "CLOSE_NEW_LEDGER_THEN_ORIGINAL_GUARDED_ENCODER",
            "publication_clock": "NEW_CURRENT_CLOCK", "encoder_loads": 0,
            "guard_acquisitions": 0, "broker_orders_sent": 0,
        }

    def read(self, item, now):
        rid = item["request"]["request_id"]
        require(isinstance(rid, str) and re.fullmatch(r"[0-9a-f]{32}", rid), "SHARED_REQUEST_ID")
        folder = self.ledger_root / "requests" / rid
        terminal_path = folder / "terminal.json"
        if not terminal_path.exists():
            return None
        terminal, terminal_sha = _captured(terminal_path)
        for key, expected in (
            ("plan_id", self.plan["plan_id"]), ("request_id", rid),
            ("request_sha256", item["request_sha256"]),
            ("producer_manifest_sha256", self.plan["producer_registration"]["sha256"]),
        ):
            require(terminal.get(key) == expected, "SHARED_SOURCE_TERMINAL_IDENTITY:" + key)
        # A failed/late source attempt cannot grant features, even if it left a
        # complete ShadowStore publication. Its original terminal is immutable.
        if terminal.get("status") != "SEALED_AWAITING_ORIGINAL_COMMAND":
            return None
        self.check_pins()
        require(load(self.ledger_root / "protocol.json") == self.protocol, "SHARED_SOURCE_PROTOCOL")
        original, input_sha = _captured(folder / "input.json")
        require(original.get("plan_id") == self.plan["plan_id"]
                and original.get("request_item") == item, "SHARED_ORIGINAL_REQUEST_CHANGED")
        require(not (folder / "original-command.json").exists(), "SHARED_ORIGINAL_COMMAND_ALREADY_OBSERVED")
        attempt, attempt_sha = _captured(folder / "attempt.json")
        for key, expected in (("plan_id", self.plan["plan_id"]), ("request_id", rid),
                              ("request_sha256", item["request_sha256"])):
            require(attempt.get(key) == expected, "SHARED_SOURCE_ATTEMPT_IDENTITY:" + key)
        prediction = self.store.prediction(rid)
        require(prediction is not None and terminal.get("publication") == prediction,
                "SHARED_SOURCE_SUCCESS_PUBLICATION")
        request = item["request"]
        deadline = min(request["started_at_ms"] + self.protocol["max_request_age_ms"], self.plan["input_end_ms"])
        start = W.integer(attempt["started_at_ms"])
        observed = W.integer(terminal["publication_durability_observed_at_ms"])
        require(attempt["deadline_ms"] == deadline
                and request["started_at_ms"] <= start <= prediction["committed_at_ms"] <= observed < deadline
                and observed <= W.integer(terminal["observed_at_ms"]) <= now,
                "SHARED_SOURCE_SUCCESS_CLOCK")
        require(0 <= W.finite(terminal["elapsed_ms"]) < deadline - start,
                "SHARED_SOURCE_MONOTONIC_DEADLINE")
        publication, publication_sha = _captured(prediction["publication_path"], prediction["publication_sha256"])
        features, features_sha = _captured(self.study_root / "predictions" / rid / "features.json",
                                           publication["features_sha256"])
        require(prediction["feature_digest"] == W.digest(features), "SHARED_SOURCE_FEATURE_DIGEST")
        require(features.get("schema") == W.FEATURE_SCHEMA and features.get("feature_profile") == W.FEATURE_PROFILE,
                "SHARED_SOURCE_FEATURE_SCHEMA")
        for key, expected in (
            ("request_id", rid), ("request_sha256", item["request_sha256"]),
            ("producer_manifest_sha256", self.plan["producer_registration"]["sha256"]),
            ("period_sha256", self.plan["period_sha256"]), ("base_id", self.plan["base_id"]),
            ("backend", self.plan["encoder_backend"]), ("encoder_threads", self.plan["encoder_threads"]),
            ("encoder_batch_size", self.plan["encoder_batch_size"]),
            ("input_started_at_ms", request["started_at_ms"]),
        ):
            require(features.get(key) == expected, "SHARED_SOURCE_FEATURE_IDENTITY:" + key)
        require(request["started_at_ms"] <= W.integer(features["computed_at_ms"])
                <= prediction["computed_at_ms"], "SHARED_SOURCE_FEATURE_CLOCK")
        W.validate_features(features["features"])
        W.validate_runtime_proof(self.plan, features)
        lineage = {
            "schema": "heptatrader.current100k-shared-feature-consumption.v1",
            "source_plan_id": self.plan["plan_id"], "source": copy.deepcopy(self.reference),
            "source_input_sha256": input_sha, "source_attempt_sha256": attempt_sha,
            "source_terminal_sha256": terminal_sha, "source_publication_sha256": publication_sha,
            "source_observation_sha256": prediction["observation_sha256"],
            "source_features_sha256": features_sha, "source_feature_digest": prediction["feature_digest"],
            "source_publication_observed_at_ms": observed,
            "encoder_recomputed": False, "new_publication_uses_current_clock": True,
        }
        return features, lineage


def run_shared_features(config, *, source=None, head_factory=None, clock=wall_ms,
                        monotonic=time.monotonic, monotonic_ns=time.monotonic_ns,
                        sleep=time.sleep, stop=None, max_seconds=None):
    """Observe until a fixed window or stop, then close before returning handoff.

    ``max_seconds`` is a test bound. The live CPU-head observer does not consume
    the encoder's 1,800-second residency budget or periodically abandon inputs.
    The caller already validated the config/release and set CPU affinity/env.
    """
    require(max_seconds is None or (type(max_seconds) in (int, float) and 0 < max_seconds <= 1800),
            "SHARED_TEST_TIME_BOUND")
    poll = config.get("poll_ms", 250) / 1000
    require(.05 <= poll <= 2, "SHARED_POLL_BOUND")
    store = W.ShadowStore(Path(config["study_plan_path"]).parent)
    shared = SharedFeatureSource(config, store)
    ledger = RequestLedger(config["state_root"], protocol_for(config, store.plan),
                           clock=clock, monotonic=monotonic_ns)
    started, started_clock = monotonic(), clock()
    requested_stop = [False]
    handlers = {}
    if stop is None:
        def receive_stop(*_):
            requested_stop[0] = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            handlers[sig] = signal.signal(sig, receive_stop)
        stop = lambda: requested_stop[0]
    heads = None
    attempts = forwards = waits = errors = busy = head_loads = 0
    remembered = {}
    reason = "BOUNDED_TEST_SESSION_COMPLETE"
    ingress = source

    def capture():
        deadline = ledger.pending_retry_deadline_ms()
        if source is None:
            # ResidentIngress's canonical API is keyword-only. Do not depend
            # on an optional read_with_deadline alias in a particular release.
            snapshot = ingress.read(retry_deadline_ms=deadline)
        else:
            bounded = getattr(ingress, "read_with_deadline", None)
            snapshot = bounded(deadline) if bounded is not None else ingress.read()
        _, requests, bindings, issues = snapshot
        pending = ledger.pending(requests)
        if not issues:
            fresh = []
            for binding in bindings:
                cid = binding["command"]["command_id"]
                require(cid not in remembered or remembered[cid] == binding, "SHARED_COMMAND_CHANGED")
                if cid not in remembered:
                    fresh.append(binding)
                    remembered[cid] = copy.deepcopy(binding)
            if fresh:
                store.remember_commands(fresh)
            for binding in bindings:
                ledger.reconcile(binding)
        return requests, pending, bindings, issues

    def still_current(item):
        require(not stop(), "SHARED_STOP_REQUESTED")
        requests, _, bindings, issues = capture()
        require(not issues, "SHARED_SOURCE_PROVENANCE")
        rid = item["request"]["request_id"]
        require(requests.get(rid) == item, "SHARED_REQUEST_DISAPPEARED_OR_CHANGED")
        require(not any(binding["request_id"] == rid for binding in bindings)
                and ledger.terminal(rid) is None, "SHARED_ORIGINAL_COMMAND_ALREADY_OBSERVED")
        now = clock()
        require(item["request"]["started_at_ms"] <= now < ledger.deadline(item["request"])
                and now < shared.plan["input_end_ms"], "SHARED_REQUEST_OR_SOURCE_WINDOW_EXPIRED")

    try:
        publish_once(ledger.root / "shared-feature-source.json", shared.binding())
        ledger.recover()
        if ingress is None:
            ingress = ResidentIngress(config["source_registration"], config["period_sha256"])
        last_clock = started_clock
        while True:
            if stop():
                reason = "SUPERVISOR_STOP"
                break
            now = clock()
            if now < last_clock:
                reason = "CLOCK_REVERSED_NO_HANDOFF"
                ledger.incident(reason)
                break
            last_clock = now
            if now >= store.plan["input_end_ms"]:
                reason = "FIXED_INPUT_WINDOW_CLOSED"
                break
            if now >= shared.plan["input_end_ms"]:
                shared.check_pins()
                reason = "SOURCE_FIXED_INPUT_WINDOW_CLOSED"
                break
            if max_seconds is not None and monotonic() - started >= max_seconds:
                break
            if heads is None:
                if head_factory is None:
                    import torch
                    torch.set_num_threads(config["encoder_threads"])
                    # The later guarded encoder owns its one interop
                    # initialization. Never touch CUDA from this phase.
                    heads = W.HeadEnsemble(store.plan)
                else:
                    heads = head_factory(store.plan)
                head_loads += 1
                publish_once(ledger.root / ("shared-heads-ready-" + uuid.uuid4().hex + ".json"), {
                    "plan_id": store.plan["plan_id"], "completed_at_ms": clock(),
                    "head_count": len(store.plan["heads"]), "head_execution_backend": "TORCH_CPU_FP32",
                    "encoder_loads": 0, "guard_acquisitions": 0, "broker_orders_sent": 0,
                })
                # Warm the cheap heads during the new registration's waiting
                # period, without spending the first request's command deadline.
                sleep(poll)
                continue
            try:
                _, pending, _, issues = capture()
                require(not issues, "SHARED_SOURCE_PROVENANCE:" + str(issues)[:350])
                for item in pending:
                    rid = item["request"]["request_id"]
                    if ledger.terminal(rid) is not None:
                        continue
                    available = shared.read(item, clock())
                    if available is None:
                        waits += 1
                        ledger.wait(rid, "WAIT_ORIGINAL_SUCCESSFUL_SHARED_FEATURES")
                        continue
                    features, lineage = available

                    def predict_and_seal(current):
                        nonlocal forwards
                        still_current(current)
                        shared.check_pins()
                        document = copy.deepcopy(features)
                        document["shared_feature_source"] = {
                            **copy.deepcopy(lineage), "consumed_at_ms": clock(),
                        }
                        predictions = heads.predict(document["features"])
                        forwards += 1
                        # A command arriving during the cheap head forward must
                        # prevent a new seal. Later arrivals are reconciled below.
                        still_current(current)
                        return store.seal(current, document, predictions)

                    # Do not spend a request attempt if source end was crossed
                    # during file verification; the guarded phase may still be
                    # able to handle that unexpired request on the same ledger.
                    still_current(item)
                    ledger.attempt(item, predict_and_seal)
                    attempts += 1
                    _, _, _, after_issues = capture()
                    require(not after_issues, "SHARED_SOURCE_CHANGED_DURING_SEAL")
                    # At most one forward per poll; every attempt gets its own
                    # fresh command snapshot and fixed original deadline.
                    break
            except SourceSnapshotBusy as exc:
                busy += 1
                ledger.source_busy(str(exc))
                ledger.incident(str(exc))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors += 1
                why = "SHARED_SOURCE_UNAVAILABLE:" + type(exc).__name__ + ":" + str(exc)[:350]
                ledger.source_busy(why)
                ledger.incident(why)
            sleep(poll)
    finally:
        heads = None
        ledger.close()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    ended = clock()
    stopped = stop()
    if stopped:
        reason = "SUPERVISOR_STOP"
    result = {
        "schema": "heptatrader.current100k-shared-feature-session.v1",
        "plan_id": store.plan["plan_id"], "source_plan_id": shared.plan["plan_id"],
        "started_at_ms": started_clock, "ended_at_ms": ended, "reason": reason,
        "source_input_end_ms": shared.plan["input_end_ms"],
        "handoff_to_guarded_encoder": not stopped and reason == "SOURCE_FIXED_INPUT_WINDOW_CLOSED"
            and shared.plan["input_end_ms"] <= ended < store.plan["input_end_ms"],
        "ledger_closed": ledger.lock is None, "encoder_loads": 0, "guard_acquisitions": 0,
        "request_attempts": attempts, "head_forward_calls": forwards, "head_loads": head_loads,
        "source_wait_observations": waits, "source_error_observations": errors,
        "source_busy_observations": busy, "resident_seconds": monotonic() - started,
        "source": ingress.statistics(), "broker_orders_sent": 0, "training_steps": 0,
        "old_trial_modified": False, "publication_clock": "NEW_CURRENT_CLOCK",
    }
    publish_once(ledger.root / ("shared-session-" + uuid.uuid4().hex + ".json"), result)
    return result
