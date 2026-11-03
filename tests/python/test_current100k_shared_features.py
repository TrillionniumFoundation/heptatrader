"""Real ledgers/stores with synthetic features and a cheap head double.

No model, broker, encoder lease or original study is modified. Real FP32 head
parity is qualified separately in the Torch environment; these tests exercise
identity, durable timing, source loss, command races and the same-ledger handoff.
"""
import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import current100k_shared_features as S
from laya_cell import current100k_weight_shadow as W
from laya_cell.current100k_preopen import RequestLedger, load, publish_once
from laya_cell.current100k_preopen_evidence import PreopenEvidence
from paired_net_100k_preflight.core import feature_text


class Clock:
    def __init__(self, ms=1010):
        self.ms, self.ns, self.sleeps = ms, 0, 0

    def wall(self):
        return self.ms

    def monotonic(self):
        return self.ns / 1e9

    def monotonic_ns(self):
        return self.ns

    def sleep(self, seconds):
        self.sleeps += 1
        self.ms += int(seconds * 1000)
        self.ns += int(seconds * 1e9)


class Ingress:
    def __init__(self, item):
        self.requests = {item["request"]["request_id"]: copy.deepcopy(item)}
        self.bindings, self.issues = [], []
        self.reads = 0
        self.on_read = None

    def read_with_deadline(self, deadline):
        self.reads += 1
        if self.on_read is not None:
            self.on_read(self.reads)
        return copy.deepcopy(({}, self.requests, self.bindings, self.issues))

    def statistics(self):
        return {"source_captures": self.reads}


def predictions(plan, scaled):
    return {
        arm: {"head_sha256": binding["sha256"],
              "raw_scaled": {side: scaled for side in W.SIDES},
              "raw_usd": {side: scaled * 100 for side in W.SIDES},
              "values_usd": {side: max(-15., min(15., scaled * 100)) for side in W.SIDES}}
        for arm, binding in plan["heads"].items()
    }


def plan_for(root, *, new=False, hip=False):
    registered, start = (20, 30) if new else (3, 10)
    arms = ["parent"] + ["candidate_%02d" % i for i in range(10)] if new else ["parent", "patch"]
    backend = "HIP_FP32" if hip else "CPU_FP32"
    value = {
        "schema": W.PLAN_SCHEMA, "rule": copy.deepcopy(W.RULE),
        "claim_scope": "PROSPECTIVE_VALUE_RESEARCH_ONLY", "trade_authority": False,
        "automatic_selection": False, "actor_update": False,
        "feature_profile": W.FEATURE_PROFILE, "base_id": "b" * 64,
        "period_sha256": "1" * 64, "behavior_model_id": "synthetic-original-model",
        "producer_registration": {"manifest": str(root.parent / "producer.json"), "sha256": "2" * 64},
        "training_dataset_path": str(root / "development.json"), "training_dataset_sha256": "3" * 64,
        "training_mean_usd": -1., "reward_config_path": str(root / "reward-config.json"),
        "reward_config_sha256": "c" * 64, "training_producer_manifest_sha256": "d" * 64,
        "cross_producer_transfer": True, "training_input_ids": ["e" * 64],
        "training_reward_ids": ["f" * 64], "training_row_ids": ["a" * 64],
        "training_cutoff_ms": 1, "labels_observed_at_ms": 2, "registered_at_ms": registered,
        "input_start_ms": start, "input_end_ms": start + W.RULE["window_ms"],
        "outcome_end_ms": start + W.RULE["window_ms"] + W.RULE["closure_grace_ms"],
        "heads": {arm: {"path": str(root / (arm + ".safetensors")), "sha256": W.digest(arm),
                         "construction_kind": "SYNTHETIC_TEST_ONLY"} for arm in arms},
        "parent_arm": "parent", "cluster_policy": "UTC_DAYS_MERGED_BY_OVERLAPPING_EPISODES",
        "bootstrap_repetitions": 2000, "bootstrap_seed": 20261103,
        "numeric_profile": W.numeric_profile(backend), "encoder_backend": backend,
        "encoder_threads": 1, "encoder_batch_size": 1, "head_execution_backend": "TORCH_CPU_FP32",
        "hip_runtime_manifest_sha256": "7" * 64 if hip else None,
    }
    value["plan_id"] = W.digest(value)
    W.validate_plan(value)
    return value


class SharedFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.clock = Clock()
        self.head_loads, self.head_calls = 0, 0
        self.head_hook = None
        self.patch_clock = patch.object(W, "clock_ms", self.clock.wall)
        self.patch_clock.start()
        self.addCleanup(self.patch_clock.stop)
        self.build()

    def config_for(self, root, plan):
        return {
            "schema": "heptatrader.current100k-preopen-research-config.v1",
            "trade_authority": False, "automatic_selection": False, "actor_update": False,
            "study_plan_path": str(root / "plan.json"), "study_plan_sha256": W.sha(root / "plan.json"),
            "source_registration": copy.deepcopy(plan["producer_registration"]),
            "period_sha256": plan["period_sha256"], "encoder_backend": plan["encoder_backend"],
            "encoder_threads": 1, "encoder_batch_size": 1, "cpu_affinity": [0], "poll_ms": 50,
            "state_root": str(root / "preopen"), "max_request_age_ms": 60000, "max_requests": 10,
            "encoder_runtime_files_sha256": {"research/paired_net_100k_preflight/core.py": "9" * 64},
        }

    def build(self, *, hip=False, success=True):
        self.old_root, self.new_root = self.root / "original", self.root / "new-family"
        self.old_plan, self.new_plan = plan_for(self.old_root, hip=hip), plan_for(self.new_root, new=True, hip=hip)
        for root, plan in ((self.old_root, self.old_plan), (self.new_root, self.new_plan)):
            W.durable_once(root / "plan.json", plan)
            W.durable_once(root / "registration.json", {
                "plan_id": plan["plan_id"], "plan_sha256": W.sha(root / "plan.json"),
                "durable_at_ms": plan["registered_at_ms"] + 1, "local_receipt_only": True,
            })
        self.old_config = self.config_for(self.old_root, self.old_plan)
        W.durable_once(self.old_root / "predictor-config.json", self.old_config)
        W.durable_once(self.old_root / "activation.json", {
            "plan_id": self.old_plan["plan_id"], "activated_at_ms": 5,
            "predictor_config_sha256": W.sha(self.old_root / "predictor-config.json"),
        })
        self.config = self.config_for(self.new_root, self.new_plan)
        self.config["shared_feature_source"] = {"schema": S.SOURCE_SCHEMA, **{
            name: {"path": str(self.old_root / filename), "sha256": W.sha(self.old_root / filename)}
            for name, filename in S.SOURCE_FILES.items()
        }}
        W.durable_once(self.new_root / "predictor-config.json", self.config)
        W.durable_once(self.new_root / "activation.json", {
            "plan_id": self.new_plan["plan_id"], "activated_at_ms": 25,
            "predictor_config_sha256": W.sha(self.new_root / "predictor-config.json"),
        })
        request = {"request_id": "a" * 32, "started_at_ms": 1000, "instrument": "EUR.USD",
                   "period_sha256": self.old_plan["period_sha256"], "position_at_request": 0,
                   "model_id": self.old_plan["behavior_model_id"], "prompt": "Synthetic causal request"}
        request["prompt_sha256"] = hashlib.sha256(request["prompt"].encode()).hexdigest()
        self.item = {"request": request, "request_sha256": W.digest(request),
                     "producer_manifest_sha256": self.old_plan["producer_registration"]["sha256"],
                     "source_record_sha256": "6" * 64, "source_written_at_ms": 1000,
                     "source_prefix": {"synthetic": True}}
        self.features = {
            "schema": W.FEATURE_SCHEMA, "feature_profile": W.FEATURE_PROFILE,
            "backend": self.old_plan["encoder_backend"], "encoder_threads": 1, "encoder_batch_size": 1,
            "request_id": request["request_id"], "request_sha256": self.item["request_sha256"],
            "producer_manifest_sha256": self.item["producer_manifest_sha256"],
            "period_sha256": request["period_sha256"], "base_id": self.old_plan["base_id"],
            "input_started_at_ms": 1000, "computed_at_ms": 1005,
            "features": {"BUY": [1.] + [0.] * 767, "SELL": [0., 1.] + [0.] * 766},
            "feature_text_sha256": {side: hashlib.sha256(feature_text(request, side).encode()).hexdigest()
                                    for side in W.SIDES},
            "hip_runtime_manifest_sha256": self.old_plan["hip_runtime_manifest_sha256"],
        }
        if hip:
            from laya_cell.resident_hip_runtime import LIBRARIES
            self.features["hip_runtime_proof"] = {
                "schema": "heptatrader.resident-hip-runtime-proof.v1", "phase": "BEFORE_PUBLISH",
                "target": "predict", "manifest_sha256": "7" * 64, "candidate_sha256": "8" * 64,
                "pid": 123, "worker_admission_granted": False, "extra_gpu_probe_performed": False,
                "libraries": {name: {"loaded": True, "sha256": "8" * 64,
                                      "device_major": 1, "device_minor": 2, "inode": 3} for name in LIBRARIES},
            }
        self.old_store = W.ShadowStore(self.old_root)
        self.old_ledger = RequestLedger(self.old_config["state_root"], S.protocol_for(self.old_config, self.old_plan),
                                         clock=self.clock.wall, monotonic=self.clock.monotonic_ns)
        self.addCleanup(self.old_ledger.close)
        if success:
            terminal = self.old_ledger.attempt(self.item, lambda item: self.old_store.seal(
                item, self.features, predictions(self.old_plan, .01)))
            self.assertEqual(terminal["status"], "SEALED_AWAITING_ORIGINAL_COMMAND")
        self.clock.ms = 1100
        self.ingress = Ingress(self.item)

    def head_factory(self, plan):
        owner = self
        self.head_loads += 1

        class Heads:
            def predict(self, features):
                owner.head_calls += 1
                owner.assertEqual(features, owner.features["features"])
                if owner.head_hook is not None:
                    owner.head_hook()
                return predictions(plan, .02)

        return Heads()

    def run_shared(self, **kwargs):
        options = {"source": self.ingress, "head_factory": self.head_factory,
                   "clock": self.clock.wall, "monotonic": self.clock.monotonic,
                   "monotonic_ns": self.clock.monotonic_ns, "sleep": self.clock.sleep,
                   "stop": lambda: False, "max_seconds": .1}
        options.update(kwargs)
        return S.run_shared_features(self.config, **options)

    def new_terminal(self):
        return load(Path(self.config["state_root"]) / "requests" / self.item["request"]["request_id"] / "terminal.json")

    def command(self, created=1100):
        return {"request_id": self.item["request"]["request_id"], "request_sha256": self.item["request_sha256"],
                "request_started_at_ms": self.item["request"]["started_at_ms"],
                "producer_manifest_sha256": self.item["producer_manifest_sha256"],
                "decision_sha256": "0" * 64,
                "command": {"command_id": "original-command", "created_at_ms": created,
                            "kind": "OPEN", "instrument": "EUR.USD", "side": "BUY"}}

    def original_bytes(self):
        return {str(path.relative_to(self.old_root)): path.read_bytes()
                for path in self.old_root.rglob("*") if path.is_file()}

    def test_shared_success_is_new_plan_current_clock_and_original_bytes_unchanged(self):
        before = self.original_bytes()
        original = self.old_store.prediction(self.item["request"]["request_id"])
        result = self.run_shared()
        self.assertEqual((result["encoder_loads"], result["guard_acquisitions"]), (0, 0))
        self.assertEqual((self.head_loads, self.head_calls), (1, 1))
        self.assertTrue(result["ledger_closed"])
        self.assertFalse(result["handoff_to_guarded_encoder"])
        prediction = W.ShadowStore(self.new_root).prediction(self.item["request"]["request_id"])
        self.assertEqual(prediction["plan_id"], self.new_plan["plan_id"])
        self.assertEqual(prediction["computed_at_ms"], 1150)
        self.assertEqual(prediction["committed_at_ms"], 1150)
        self.assertGreater(prediction["committed_at_ms"], original["committed_at_ms"])
        self.assertEqual(len(prediction["predictions"]), 11)
        self.assertEqual(prediction["predictions"]["parent"]["values_usd"]["BUY"], 2.)
        self.assertEqual(self.new_terminal()["status"], "SEALED_AWAITING_ORIGINAL_COMMAND")
        feature_path = self.new_root / "predictions" / self.item["request"]["request_id"] / "features.json"
        features = W.read(feature_path)
        self.assertEqual(features["features"], self.features["features"])
        self.assertEqual(features["computed_at_ms"], 1005)
        lineage = features["shared_feature_source"]
        self.assertEqual(lineage["source_publication_sha256"], original["publication_sha256"])
        self.assertEqual(lineage["consumed_at_ms"], 1150)
        self.assertEqual(prediction["feature_digest"], W.digest(features))
        self.assertEqual(before, self.original_bytes())

    def test_existing_command_never_runs_heads_or_creates_attempt(self):
        self.ingress.bindings = [self.command()]
        self.run_shared()
        self.assertEqual(self.head_calls, 0)
        self.assertEqual(self.new_terminal()["status"], "NO_PREDICTION_BEFORE_ORIGINAL_COMMAND")
        self.assertFalse((Path(self.config["state_root"]) / "requests" / ("a" * 32) / "attempt.json").exists())
        self.assertFalse((self.new_root / "predictions").exists())

    def test_command_arriving_during_head_forward_prevents_seal(self):
        self.head_hook = lambda: self.ingress.bindings.append(self.command())
        self.run_shared()
        self.assertEqual(self.head_calls, 1)
        self.assertEqual(self.new_terminal()["status"], "NO_PREDICTION_BEFORE_ORIGINAL_COMMAND")
        self.assertFalse((self.new_root / "predictions").exists())

    def test_command_arriving_during_seal_gets_no_preopen_credit(self):
        original_seal = W.ShadowStore.seal

        def racing_seal(store, *args):
            result = original_seal(store, *args)
            self.ingress.bindings.append(self.command())
            return result

        with patch.object(W.ShadowStore, "seal", racing_seal):
            self.run_shared()
        bound = load(Path(self.config["state_root"]) / "bindings" / "original-command.json")
        self.assertEqual(bound["status"], "MISSING_OR_LATE_NO_CREDIT")
        self.assertFalse(bound["before_original_command"])

    def test_later_command_and_restart_use_strict_new_evidence_without_replay(self):
        self.run_shared()
        self.clock.ms = 1300
        binding = self.command(created=1200)
        self.ingress.bindings = [binding]
        result = self.run_shared()
        self.assertEqual(result["request_attempts"], 0)
        self.assertEqual(self.head_calls, 1)
        prediction = W.ShadowStore(self.new_root).prediction("a" * 32)
        checked = PreopenEvidence(self.new_root).check(binding, prediction)
        self.assertEqual(checked["prediction_terminal"], "SEALED_AWAITING_ORIGINAL_COMMAND")

    def test_failed_source_terminal_cannot_reuse_a_complete_publication(self):
        path = Path(self.old_config["state_root"]) / "requests" / ("a" * 32) / "terminal.json"
        terminal = load(path)
        terminal["status"] = "PREDICTION_FAILED_OR_LATE"
        W.atomic(path, terminal)
        result = self.run_shared()
        self.assertEqual((self.head_calls, result["request_attempts"]), (0, 0))
        self.assertGreater(result["source_wait_observations"], 0)
        self.assertFalse((self.new_root / "predictions").exists())

    def test_missing_source_waits_then_expires_without_encoder_fallback(self):
        path = Path(self.old_config["state_root"]) / "requests" / ("a" * 32) / "terminal.json"
        path.unlink()
        first = self.run_shared()
        self.assertGreater(first["source_wait_observations"], 0)
        self.assertFalse(first["handoff_to_guarded_encoder"])
        self.clock.ms = 61000
        self.run_shared()
        self.assertEqual(self.new_terminal()["status"], "REQUEST_EXPIRED_NO_BACKDATING")
        self.assertEqual(self.head_calls, 0)

    def test_default_resident_keyword_api_keeps_pending_deadline_during_busy_capture(self):
        owner = self

        class NativeReader:
            def __init__(self, registration, period):
                self.deadlines = []
                owner.assertEqual(registration, owner.config["source_registration"])
                owner.assertEqual(period, owner.config["period_sha256"])

            def read(self, *, retry_deadline_ms=None):
                self.deadlines.append(retry_deadline_ms)
                if len(self.deadlines) == 2:
                    raise S.SourceSnapshotBusy("SYNTHETIC_APPEND_RACE", 1)
                return owner.ingress.read_with_deadline(retry_deadline_ms)

            def statistics(self):
                return {"source_captures": len(self.deadlines)}

        reader = NativeReader(self.config["source_registration"], self.config["period_sha256"])
        self.assertFalse(hasattr(reader, "read_with_deadline"))
        with patch.object(S, "ResidentIngress", return_value=reader):
            result = self.run_shared(source=None)
        self.assertEqual(reader.deadlines, [None, 61000])
        self.assertEqual(result["source_busy_observations"], 1)
        self.assertEqual((result["request_attempts"], self.head_calls), (0, 0))
        self.assertFalse((Path(self.config["state_root"]) / "requests" / ("a" * 32) / "attempt.json").exists())

    def test_changed_source_feature_file_cannot_reach_new_heads(self):
        path = self.old_root / "predictions" / ("a" * 32) / "features.json"
        document = W.read(path)
        document["features"]["BUY"] = document["features"]["SELL"]
        W.atomic(path, document)
        result = self.run_shared()
        self.assertGreater(result["source_error_observations"], 0)
        self.assertEqual(self.head_calls, 0)
        self.assertFalse((self.new_root / "predictions").exists())

    def test_source_pin_drift_rejected_before_new_ledger(self):
        path = self.old_root / "activation.json"
        value = W.read(path)
        value["activated_at_ms"] = 6
        W.atomic(path, value)
        with self.assertRaisesRegex(ValueError, "SHARED_PIN_CHANGED"):
            self.run_shared()
        self.assertFalse(Path(self.config["state_root"]).exists())

    def test_other_base_producer_or_numeric_profile_cannot_share_features(self):
        for key, value in (("base_id", "0" * 64), ("encoder_threads", 2),
                           ("producer_registration", {"sha256": "0" * 64})):
            with self.subTest(key=key):
                store = W.ShadowStore(self.new_root)
                store.plan[key] = value
                store.plan["plan_id"] = W.digest({k: v for k, v in store.plan.items() if k != "plan_id"})
                W.validate_plan(store.plan)
                with self.assertRaisesRegex(ValueError, "SHARED_NUMERIC_OR_SOURCE_SCOPE:" + key):
                    S.SharedFeatureSource(self.config, store)

    def test_encoder_loader_pin_must_match_the_later_guarded_fallback(self):
        config = copy.deepcopy(self.config)
        config["encoder_runtime_files_sha256"]["learn/laya_cell/model.py"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHARED_ENCODER_RUNTIME_CONTRACT"):
            S.SharedFeatureSource(config, W.ShadowStore(self.new_root))

    def test_hip_feature_proof_is_retained_without_new_gpu_context(self):
        self.root = self.root / "hip-fixture"
        self.clock.ms = 1010
        self.build(hip=True)
        result = self.run_shared()
        self.assertEqual(result["encoder_loads"], 0)
        feature_path = self.new_root / "predictions" / ("a" * 32) / "features.json"
        features = W.read(feature_path)
        self.assertEqual(features["hip_runtime_proof"], self.features["hip_runtime_proof"])
        W.validate_runtime_proof(self.new_plan, features)
        self.assertEqual(self.new_terminal()["status"], "SEALED_AWAITING_ORIGINAL_COMMAND")

    @unittest.skipUnless(importlib.util.find_spec("torch") and importlib.util.find_spec("safetensors"),
                         "Real FP32 heads run in the Torch qualification environment")
    def test_real_eleven_fp32_heads_match_direct_shared_feature_inference(self):
        import torch
        from safetensors.torch import save_file
        for index, (arm, binding) in enumerate(sorted(self.new_plan["heads"].items())):
            weight = torch.zeros((1, 768), dtype=torch.float32)
            weight[0, index % 2] = (index + 1) / 128
            save_file({"value.weight": weight, "value.bias": torch.zeros(1, dtype=torch.float32)}, binding["path"])
            binding["sha256"] = W.sha(binding["path"])
        self.new_plan["plan_id"] = W.digest({key: value for key, value in self.new_plan.items() if key != "plan_id"})
        W.atomic(self.new_root / "plan.json", self.new_plan)
        registration = W.read(self.new_root / "registration.json")
        registration.update(plan_id=self.new_plan["plan_id"], plan_sha256=W.sha(self.new_root / "plan.json"))
        W.atomic(self.new_root / "registration.json", registration)
        self.config["study_plan_sha256"] = W.sha(self.new_root / "plan.json")
        W.atomic(self.new_root / "predictor-config.json", self.config)
        activation = W.read(self.new_root / "activation.json")
        activation.update(plan_id=self.new_plan["plan_id"],
                          predictor_config_sha256=W.sha(self.new_root / "predictor-config.json"))
        W.atomic(self.new_root / "activation.json", activation)
        expected = W.HeadEnsemble(self.new_plan).predict(self.features["features"])
        result = self.run_shared(head_factory=None)
        actual = W.ShadowStore(self.new_root).prediction("a" * 32)
        self.assertEqual(actual["predictions"], expected)
        self.assertEqual(len(actual["predictions"]), 11)
        self.assertEqual((result["encoder_loads"], result["head_forward_calls"]), (0, 1))
        self.assertEqual(self.new_terminal()["status"], "SEALED_AWAITING_ORIGINAL_COMMAND")

    def test_interrupted_new_attempt_is_never_replayed(self):
        ledger = RequestLedger(self.config["state_root"], S.protocol_for(self.config, self.new_plan),
                               clock=self.clock.wall, monotonic=self.clock.monotonic_ns)
        try:
            folder = ledger.observe(self.item)
            publish_once(folder / "attempt.json", {"synthetic_interruption": True})
        finally:
            ledger.close()
        self.run_shared()
        self.assertEqual(self.new_terminal()["status"], "INTERRUPTED_UNKNOWN_NO_REPLAY")
        self.assertEqual(self.head_calls, 0)

    def test_source_end_releases_same_new_ledger_before_guarded_handoff(self):
        self.clock.ms = self.old_plan["input_end_ms"]
        result = self.run_shared(max_seconds=None)
        self.assertEqual(result["reason"], "SOURCE_FIXED_INPUT_WINDOW_CLOSED")
        self.assertTrue(result["handoff_to_guarded_encoder"])
        self.assertTrue(result["ledger_closed"])
        self.assertEqual(self.head_loads, 0)
        # The existing guarded runner can acquire this exact protocol immediately;
        # the original source ledger is still locked by its original owner.
        reopened = RequestLedger(self.config["state_root"], S.protocol_for(self.config, self.new_plan))
        reopened.close()
        with self.assertRaises(BlockingIOError):
            RequestLedger(self.old_config["state_root"], S.protocol_for(self.old_config, self.old_plan))

    def test_stop_and_new_window_end_never_grant_handoff(self):
        self.clock.ms = self.old_plan["input_end_ms"]
        stopped = self.run_shared(stop=lambda: True, max_seconds=None)
        self.assertEqual(stopped["reason"], "SUPERVISOR_STOP")
        self.assertFalse(stopped["handoff_to_guarded_encoder"])
        self.clock.ms = self.new_plan["input_end_ms"]
        ended = self.run_shared(max_seconds=None)
        self.assertEqual(ended["reason"], "FIXED_INPUT_WINDOW_CLOSED")
        self.assertFalse(ended["handoff_to_guarded_encoder"])

    def test_shared_observer_does_not_apply_encoder_residency_limit(self):
        self.ingress.requests = {}

        def long_idle(_):
            self.clock.sleep(2000)

        result = self.run_shared(max_seconds=None, sleep=long_idle, stop=lambda: self.clock.sleeps >= 2)
        self.assertEqual(result["resident_seconds"], 4000)
        self.assertEqual(result["reason"], "SUPERVISOR_STOP")
        self.assertEqual(self.ingress.reads, 1)
        self.assertFalse(result["handoff_to_guarded_encoder"])


class GuardedHandoffIntegrationTests(unittest.TestCase):
    def test_handoff_revalidates_dependencies_before_encoder_admission(self):
        import run_current100k_preopen_research as runner

        config = {"encoder_threads": 2, "encoder_device": "cpu",
                  "cpu_affinity": [0, 1], "shared_feature_source": {}}
        calls = []

        def validate(value):
            self.assertIs(value, config)
            calls.append("validate")
            if len(calls) > 1:
                raise ValueError("DEPENDENCY_CHANGED_DURING_SHARED_PHASE")

        with patch.object(runner, "validate_config", side_effect=validate), \
                patch.object(runner.os, "sched_setaffinity"), \
                patch.dict(runner.os.environ), \
                patch.object(S, "run_shared_features", return_value={"handoff_to_guarded_encoder": True}), \
                patch.object(runner, "original_guard") as guard:
            with self.assertRaisesRegex(ValueError, "DEPENDENCY_CHANGED_DURING_SHARED_PHASE"):
                runner.run(config)
        self.assertEqual(calls, ["validate", "validate"])
        guard.assert_not_called()

    def test_stopped_shared_phase_returns_without_encoder_or_revalidation(self):
        import run_current100k_preopen_research as runner

        config = {"encoder_threads": 2, "encoder_device": "cpu",
                  "cpu_affinity": [0, 1], "shared_feature_source": {}}
        result = {"handoff_to_guarded_encoder": False, "reason": "SUPERVISOR_STOP"}
        with patch.object(runner, "validate_config") as validate, \
                patch.object(runner.os, "sched_setaffinity"), \
                patch.dict(runner.os.environ), \
                patch.object(S, "run_shared_features", return_value=result), \
                patch.object(runner, "original_guard") as guard:
            self.assertIs(runner.run(config), result)
        validate.assert_called_once_with(config)
        guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
