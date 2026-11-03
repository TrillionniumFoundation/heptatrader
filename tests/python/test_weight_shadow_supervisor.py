import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_current100k_weight_shadow as fixtures
import test_current100k_weight_continuation as continuation_fixtures
import supervise_current100k_weight_shadow as S
from laya_cell import current100k_weight_shadow as W
from laya_cell import current100k_weight_shadow_eval as E

class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ShadowTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.study
        self.plan = copy.deepcopy(self.fixture.p)
        self.plan.update(head_execution_backend="TORCH_CPU_FP32", hip_runtime_manifest_sha256=None)
        self.plan["plan_id"] = W.digest({k:v for k,v in self.plan.items() if k != "plan_id"})
        W.atomic(self.root / "plan.json", self.plan)
        W.atomic(self.root / "registration.json", {"plan_id":self.plan["plan_id"],"plan_sha256":W.sha(self.root / "plan.json"),"durable_at_ms":4})
        self.config = {"_sha256":"1"*64,"study_config_sha256":"2"*64}
        self.template = {"trade_authority":False}
    def test_restart_preserves_registered_window_and_existing_activation(self):
        with patch.object(W, "freeze", side_effect=AssertionError("must not allocate another window")):
            plan, _ = S.ensure_activation(self.config, self.plan, self.template, self.root)
            before = (self.root / "activation.json").read_bytes()
            again, _ = S.ensure_activation(self.config, self.plan, self.template, self.root)
        self.assertEqual(before, (self.root / "activation.json").read_bytes())
        self.assertEqual(plan["input_start_ms"], again["input_start_ms"])
        self.assertEqual(plan["outcome_end_ms"], again["outcome_end_ms"])
    def test_changed_candidate_set_cannot_reuse_window(self):
        other = copy.deepcopy(self.plan)
        other["heads"]["patch"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(Exception, "EXISTING_PLAN_DIFFERENT"):
            S.ensure_activation(self.config, other, self.template, self.root)
        self.assertFalse((self.root / "activation.json").exists())
    def test_crashed_registration_is_not_backfilled(self):
        (self.root / "registration.json").unlink()
        with patch.object(W, "freeze", side_effect=AssertionError("must not register again")):
            with self.assertRaisesRegex(Exception, "INCOMPLETE_REGISTRATION"):
                S.ensure_activation(self.config, self.plan, self.template, self.root)
    def test_invalid_registration_receipt_cannot_activate(self):
        record = W.read(self.root / "registration.json")
        record["plan_sha256"] = "0" * 64
        W.atomic(self.root / "registration.json", record)
        with self.assertRaisesRegex(Exception, "PLAN_REGISTRATION"):
            S.ensure_activation(self.config, self.plan, self.template, self.root)
    def test_stale_or_future_input_does_not_start_experiment(self):
        now = 1000000
        for timestamp in (now - 60001, now + 1):
            _, eligible = S.fresh_request(now, {"r":{"request":{"started_at_ms":timestamp}}}, 60000)
            self.assertFalse(eligible)
        self.assertFalse(S.fresh_request(now, {}, 60000)[1])
        self.assertTrue(S.fresh_request(now, {"r":{"request":{"started_at_ms":now - 1000}}}, 60000)[1])

    def runnable_config(self):
        template = {"source_registration": self.plan["producer_registration"], "period_sha256": self.plan["period_sha256"],
                    **{key: self.plan[key] for key in ("encoder_backend", "encoder_threads", "encoder_batch_size")}}
        evidence = self.root / "qualification-evidence.json"
        W.atomic(evidence, {"synthetic_fixture": True})
        qualification = {"backend": self.plan["encoder_backend"], "tensor_cleanup_verified": True,
                         "max_request_seconds": 1, "deadline_budget_seconds": 2,
                         "evidence_path": str(evidence), "evidence_sha256": W.sha(evidence)}
        config = {**self.config, "schema": "heptatrader.weight-shadow-supervisor.v1", "trade_authority": False,
                  "automatic_selection": False, "actor_update": False, "state_root": str(self.root),
                  "native_stop_marker": str(self.root / "unused-native-stop"), "python": "/usr/bin/python3",
                  "release_manifest": "synthetic-release", "release_manifest_sha256": "1" * 64}
        for name, value in (("study_config", self.plan), ("predictor_template", template), ("qualification", qualification)):
            path = self.root / (name + ".json")
            W.atomic(path, value)
            config[name + "_path"] = str(path)
            config[name + "_sha256"] = W.sha(path)
        return config

    def pending_assessment(self):
        result = E.assessment(self.plan, [], self.plan["outcome_end_ms"] - 1000)
        W.atomic(self.root / "assessment.json", result)
        return (self.root / "assessment.json").read_bytes()

    def test_watchdog_does_not_extend_window_or_relabel_previous_result(self):
        prior = self.pending_assessment()
        plan_bytes = (self.root / "plan.json").read_bytes()
        deadline = self.plan["outcome_end_ms"] + S.FINAL_REPORT_TIMEOUT_MS
        with patch.object(W, "clock_ms", return_value=deadline + 19), patch("builtins.print"), \
             patch.object(S, "terminal_status", wraps=S.terminal_status) as status:
            stopped = []
            self.assertIsNone(S.fixed_final_status(self.root, self.plan, deadline - 1, lambda: stopped.append(True)))
            result = S.fixed_final_status(self.root, self.plan, deadline, lambda: stopped.append(True))
            self.assertEqual(stopped, [True])
            self.assertEqual(status.call_count, 1)
        self.assertEqual(result["state"], "FINAL_UNAVAILABLE_NO_SELECTION")
        self.assertEqual(result["checked_at_ms"], deadline + 19)
        self.assertEqual(result["error"]["type"], "TimeoutError")
        for key in ("observed_trades", "original_command_count", "prediction_coverage"):
            self.assertIsNone(result[key])
        self.assertEqual((self.root / "assessment.json").read_bytes(), prior)
        self.assertEqual((self.root / "plan.json").read_bytes(), plan_bytes)
        self.assertFalse(result["financial_candidate_selected"])
        terminal = E.read_terminal_unavailable(self.root, self.plan)
        self.assertEqual(terminal["diagnostics"]["publication_deadline_ms"], deadline)
        self.assertFalse(terminal["diagnostics"]["data_window_extended"])
        self.assertEqual(S.fixed_final_status(self.root, self.plan, deadline + 10000,
                                             lambda: self.fail("terminal must be reused"))["checked_at_ms"], deadline + 19)

    def test_complete_final_is_consumed_without_spawning_or_restamping(self):
        final = E.assessment(self.plan, [], self.plan["outcome_end_ms"] + 1)
        W.atomic(self.root / "assessment.json", final)
        before = (self.root / "assessment.json").read_bytes()
        result = S.fixed_final_status(self.root, self.plan, self.plan["outcome_end_ms"] + 2,
                                      lambda: self.fail("no timeout"))
        self.assertEqual(result["state"], "FINAL_ASSESSMENT_AVAILABLE")
        self.assertEqual(result["assessment_as_of_ms"], final["as_of_ms"])
        self.assertEqual((self.root / "assessment.json").read_bytes(), before)
        self.assertFalse((self.root / "terminal-unavailable.json").exists())

    def test_supervisor_terminates_when_evaluator_never_publishes(self):
        config = self.runnable_config()
        prior = self.pending_assessment()
        now = self.plan["outcome_end_ms"] + S.FINAL_REPORT_TIMEOUT_MS
        with patch.object(S, "verify_release", return_value=(S.ROOT, {})), \
             patch.object(S, "RegistrationSource"), patch.object(S, "original_guard"), \
             patch.object(W, "clock_ms", return_value=now), patch.object(S.signal, "signal"), \
             patch.object(S.subprocess, "Popen", side_effect=AssertionError("must not restart a final worker")), \
             patch.object(S.time, "sleep", side_effect=AssertionError("must not loop beyond fixed timeout")):
            result = S.run(config)
        self.assertEqual(result["state"], "FINAL_UNAVAILABLE_NO_SELECTION")
        self.assertEqual(result["error"]["message"], "FINAL_REPORT_PUBLICATION_TIMEOUT")
        self.assertFalse(result["financial_candidate_selected"])
        self.assertEqual((self.root / "assessment.json").read_bytes(), prior)

    def test_supervisor_terminalizes_actual_config_read_error_after_end(self):
        config = self.runnable_config()
        prior = self.pending_assessment()
        now = self.plan["outcome_end_ms"] + 1
        with patch.object(S, "verify_release", return_value=(S.ROOT, {})), \
             patch.object(S, "RegistrationSource"), patch.object(S, "original_guard"), \
             patch.object(W, "clock_ms", return_value=now), patch.object(S.signal, "signal"), \
             patch.object(S, "ensure_activation", side_effect=OSError("injected config read failure")), \
             patch.object(S.time, "sleep", side_effect=AssertionError("must not retry final source failure")):
            result = S.run(config)
        self.assertEqual(result["state"], "FINAL_UNAVAILABLE_NO_SELECTION")
        self.assertEqual(result["error"], {"type": "OSError", "message": "injected config read failure"})
        self.assertEqual(result["checked_at_ms"], now)
        self.assertIsNone(result["prediction_coverage"])
        self.assertEqual((self.root / "assessment.json").read_bytes(), prior)



    def test_final_source_constructor_failure_and_restart_remain_terminal(self):
        config = self.runnable_config()
        prior = self.pending_assessment()
        now = self.plan["outcome_end_ms"] + 1
        with patch.object(S, "verify_release", return_value=(S.ROOT, {})), \
             patch.object(S, "RegistrationSource", side_effect=OSError("injected source initialization read failure")) as source, \
             patch.object(S, "original_guard"), patch.object(W, "clock_ms", return_value=now), \
             patch.object(S.signal, "signal"), patch.object(S.time, "sleep", side_effect=AssertionError("must finish")):
            result = S.run(config)
            self.assertEqual(result["state"], "FINAL_UNAVAILABLE_NO_SELECTION")
            self.assertEqual(result["error"]["message"], "injected source initialization read failure")
            self.assertFalse(result["supervisor_running"])
            self.assertIsNone(result["prediction_coverage"])
            terminal = (self.root / "terminal-unavailable.json").read_bytes()
            restarted = S.run(config)
            self.assertEqual(source.call_count, 1)
            self.assertEqual(restarted["state"], "FINAL_UNAVAILABLE_NO_SELECTION")
            self.assertEqual((self.root / "terminal-unavailable.json").read_bytes(), terminal)
        self.assertEqual((self.root / "assessment.json").read_bytes(), prior)


class SourceRecoveryTests(unittest.TestCase):
    """Real append-only source parsing; synthetic payloads, no provider/orders."""
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1000000
        self.period = "1" * 64
        self.producer = "a" * 32
        self.journal = self.root / "journal"
        self.journal.mkdir(mode=0o700)
        manifest = {"schema": "heptatrader.attribution-producer.v1",
                    "period_sha256": self.period, "instrument": "EUR.USD",
                    "execution_domain": "PAPER", "journal_directory": str(self.journal)}
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps(manifest))
        self.manifest.chmod(0o600)
        self.registration = {"manifest": str(self.manifest), "sha256": W.sha(self.manifest)}
        self.records = []
        self.emit("HEADER", {"manifest_sha256": self.registration["sha256"],
                            "period_sha256": self.period, "instrument": "EUR.USD",
                            "execution_domain": "PAPER", "source_kind": "PINNED_DRIVER_CALLSITE",
                            "trading_authority": False}, self.now - 2000)
        self.source = S.RegistrationSource(self.registration, self.period, "gpt-6-luna")

    def emit(self, kind, data, timestamp=None):
        import hepta_decision_attribution as a
        at = self.now - 500 if timestamp is None else timestamp
        record = {"schema": a.SCHEMA, "producer_id": self.producer, "sequence": len(self.records),
                  "previous": self.records[-1]["sha256"] if self.records else "0" * 64,
                  "kind": kind, "captured_at_ms": at, "written_at_ms": at,
                  "dropped_total": 0, "data": copy.deepcopy(data)}
        record["sha256"] = a.digest(record)
        self.records.append(record)
        path = self.journal / (self.producer + "-0000.jsonl")
        with path.open("a") as stream:
            stream.write(a.canonical(record) + "\n")
        path.chmod(0o600)

    def documents(self, action="HOLD", *, rid=None, started=None):
        import hepta_decision_attribution as a
        request = {"request_id": rid or "b" * 32,
                   "started_at_ms": self.now - 1000 if started is None else started,
                   "instrument": "EUR.USD", "period_sha256": self.period,
                   "model_id": "gpt-6-luna", "position_at_request": 0,
                   "model_controls": {"model": "openai/gpt-5.6-luna", "local": True, "json": True},
                   "prompt": "Synthetic recovery fixture", "history_snapshot": []}
        request["prompt_sha256"] = hashlib.sha256(request["prompt"].encode()).hexdigest()
        value = {"action": action, "confidence": 0.5, "rationale": "Synthetic fixture"}
        response = {"request_id": request["request_id"], "request_sha256": a.digest(request),
                    "available_at_ms": self.now - 600, "returncode": 0,
                    "stdout": json.dumps({"ok": True, "capability": "model.run", "provider": "openai",
                                          "model": "gpt-5.6-luna", "outputs": [{"text": json.dumps(value)}]})}
        decision = {"request_id": request["request_id"], "request_sha256": a.digest(request),
                    "available_at_ms": self.now - 500, "response_sha256": a.digest(response),
                    "decision": value, "decision_sha256": a.digest(value)}
        return request, response, decision

    def relink(self, request, response, decision):
        import hepta_decision_attribution as a
        response["request_sha256"] = a.digest(request)
        decision["request_sha256"] = a.digest(request)
        decision["response_sha256"] = a.digest(response)
        decision["decision_sha256"] = a.digest(decision["decision"])

    def write_documents(self, documents, kinds=("REQUEST", "RESPONSE", "DECISION")):
        for kind in kinds:
            doc = documents[("REQUEST", "RESPONSE", "DECISION").index(kind)]
            self.emit(kind, doc)

    def readiness(self, now=None):
        manifest, requests, bindings, issues, completed = self.source.read_successes()
        self.assertEqual(issues, [])
        latest, witness = S.fresh_successful_decision(
            self.now if now is None else now, requests, completed, 60000)
        return requests, bindings, completed, latest, witness

    def test_pending_and_failed_provider_calls_do_not_start_window(self):
        docs = self.documents()
        self.write_documents(docs, ("REQUEST",))
        self.assertIsNone(self.readiness()[-1])
        docs[1].update(returncode=1, stdout="")
        self.emit("RESPONSE", docs[1])
        self.assertIsNone(self.readiness()[-1])

    def test_successful_hold_proves_recovery_without_an_open_command(self):
        self.write_documents(self.documents())
        requests, bindings, completed, latest, witness = self.readiness()
        self.assertEqual(bindings, [])
        self.assertEqual(witness["action"], "HOLD")
        self.assertEqual(witness["model_id"], "gpt-6-luna")
        self.assertEqual(witness["provider_model"], "openai/gpt-5.6-luna")
        self.assertEqual(witness["request_sha256"], next(iter(requests.values()))["request_sha256"])

    def test_response_without_original_decision_is_not_success(self):
        self.write_documents(self.documents(), ("REQUEST", "RESPONSE"))
        self.assertIsNone(self.readiness()[-1])

    def test_nonzero_provider_returncode_cannot_be_masked_by_valid_output(self):
        docs = self.documents()
        docs[1]["returncode"] = 1
        self.relink(*docs)
        self.write_documents(docs)
        self.assertIsNone(self.readiness()[-1])

    def test_wrong_actual_provider_model_is_refused(self):
        docs = self.documents()
        body = json.loads(docs[1]["stdout"])
        body["model"] = "another-model"
        docs[1]["stdout"] = json.dumps(body)
        self.relink(*docs)
        self.write_documents(docs)
        self.assertIsNone(self.readiness()[-1])

    def test_mismatched_request_and_decision_identity_is_refused(self):
        docs = self.documents()
        docs[2]["request_sha256"] = "f" * 64
        self.write_documents(docs)
        self.assertIsNone(self.readiness()[-1])

    def test_different_decision_than_original_response_is_refused(self):
        docs = self.documents()
        docs[2]["decision"]["action"] = "BUY"
        self.relink(*docs)
        self.write_documents(docs)
        self.assertIsNone(self.readiness()[-1])

    def test_decision_record_before_response_is_refused(self):
        self.write_documents(self.documents(), ("REQUEST", "DECISION", "RESPONSE"))
        self.assertIsNone(self.readiness()[-1])

    def test_invalid_success_output_never_proves_recovery(self):
        import hepta_decision_attribution as a
        docs = self.documents()
        self.write_documents(docs)
        manifest, requests, _, issues = self.source.read()
        graphs, _ = a._verified_producer_graphs(self.journal, "EUR.USD", self.period)
        rid = docs[0]["request_id"]
        invalid = ["not-json", "null", "[]", json.dumps({"action": "HOLD", "confidence": True, "rationale": ""}),
                   json.dumps({"action": "BUY", "confidence": 2, "rationale": ""}),
                   json.dumps({"action": "TRADE", "confidence": 0.5, "rationale": ""}),
                   json.dumps({"action": "HOLD", "confidence": 0.5})]
        for text in invalid:
            with self.subTest(text=text):
                altered = copy.deepcopy(graphs)
                response, decision = altered[0][3][rid], altered[0][4][rid]
                envelope = json.loads(response["stdout"])
                envelope["outputs"][0]["text"] = text
                response["stdout"] = json.dumps(envelope)
                decision["response_sha256"] = a.digest(response)
                self.assertEqual(S.successful_decisions(
                    requests, altered, self.registration["sha256"], "gpt-6-luna"), {})

    def test_original_request_age_and_completion_clock_must_both_be_valid(self):
        self.write_documents(self.documents())
        requests, _, completed, _, witness = self.readiness()
        self.assertIsNotNone(witness)
        self.assertIsNone(S.fresh_successful_decision(self.now + 60000, requests, completed, 60000)[1])
        for field, value in [("request_started_at_ms", self.now + 1),
                             ("response_available_at_ms", self.now + 1),
                             ("decision_available_at_ms", self.now + 1),
                             ("response_available_at_ms", self.now - 1001)]:
            with self.subTest(field=field):
                changed = copy.deepcopy(completed)
                next(iter(changed.values()))[field] = value
                self.assertIsNone(S.fresh_successful_decision(self.now, requests, changed, 60000)[1])

    def test_late_response_does_not_refresh_an_expired_request(self):
        self.write_documents(self.documents(started=self.now - 60001))
        self.assertIsNone(self.readiness()[-1])

    def test_newer_pending_or_failed_request_does_not_reuse_previous_success(self):
        self.write_documents(self.documents())
        self.assertIsNotNone(self.readiness()[-1])
        newer = self.documents(rid="c" * 32, started=self.now - 100)
        self.write_documents(newer, ("REQUEST",))
        self.assertIsNone(self.readiness()[-1])
        newer[1].update(returncode=1, stdout="", available_at_ms=self.now - 10)
        self.emit("RESPONSE", newer[1])
        self.assertIsNone(self.readiness()[-1])

    def test_unchanged_source_reuses_validation_but_freshness_still_expires(self):
        self.write_documents(self.documents())
        self.assertIsNotNone(self.readiness()[-1])
        with patch.object(S, "successful_decisions", side_effect=AssertionError("duplicate full validation")):
            self.assertIsNotNone(self.readiness()[-1])
            self.assertIsNone(self.readiness(self.now + 60000)[-1])

    def test_source_health_failure_revokes_readiness(self):
        self.write_documents(self.documents())
        self.assertIsNotNone(self.readiness()[-1])
        health = self.journal / (self.producer + ".health.json")
        health.write_text(json.dumps({"dropped": 1, "error": None, "at_ms": self.now}))
        health.chmod(0o600)
        _, requests, _, issues, completed = self.source.read_successes()
        self.assertTrue(issues)
        self.assertEqual(completed, {})

    def test_run_waits_on_failed_source_and_allows_successful_hold(self):
        from types import SimpleNamespace
        docs = self.documents()
        self.write_documents(docs, ("REQUEST",))
        root = self.root / "study"
        study = {"producer_registration": self.registration, "period_sha256": self.period,
                 "behavior_model_id": "gpt-6-luna", "encoder_backend": "cpu_fp32",
                 "encoder_threads": 2, "encoder_batch_size": 1}
        template = dict(study, source_registration=self.registration)
        evidence = self.root / "qualification-evidence.json"
        evidence.write_text("{}")
        qualification = {"backend": "cpu_fp32", "tensor_cleanup_verified": True,
                         "max_request_seconds": 1, "deadline_budget_seconds": 60,
                         "evidence_path": str(evidence), "evidence_sha256": W.sha(evidence)}
        config = {"schema": "heptatrader.weight-shadow-supervisor.v1", "trade_authority": False,
                  "automatic_selection": False, "actor_update": False,
                  "release_manifest": "unused", "release_manifest_sha256": "unused",
                  "study_config_path": "study", "study_config_sha256": "unused",
                  "predictor_template_path": "template", "predictor_template_sha256": "unused",
                  "qualification_path": "qualification", "qualification_sha256": "unused",
                  "state_root": str(root), "native_stop_marker": str(self.root / "no-native-stop")}
        documents = {"study": study, "template": template, "qualification": qualification}
        guard = SimpleNamespace(check=lambda: {"admit_new_worker": True})
        def step(admission_delay=0):
            def admission():
                self.now += admission_delay
                return {"admit_new_worker": True}
            guard.check = admission
            with patch.object(S, "verify_release", return_value=(S.ROOT, None)), \
                    patch.object(S, "pinned_json", side_effect=lambda path, _: documents[path]), \
                    patch.object(S, "original_guard", return_value=guard), \
                    patch.object(S.signal, "signal"), patch.object(W, "clock_ms", side_effect=lambda: self.now), \
                    patch.object(S.time, "sleep", side_effect=StopIteration("end test turn")), \
                    patch.object(S, "ensure_activation") as activation:
                with self.assertRaises(StopIteration):
                    S.run(config)
            return activation
        self.assertFalse(step().called)
        self.assertFalse((root / "plan.json").exists())
        docs[1].update(returncode=1, stdout="")
        self.emit("RESPONSE", docs[1])
        self.assertFalse(step().called)
        self.assertFalse((root / "plan.json").exists())
        successful = self.documents(rid="d" * 32, started=self.now - 400)
        successful[1]["available_at_ms"] = self.now - 200
        successful[2]["available_at_ms"] = self.now - 100
        self.relink(*successful)
        self.write_documents(successful)
        activated = step()
        self.assertEqual(activated.call_count, 1)
        self.assertEqual(activated.call_args.kwargs["source_recovery"]["action"], "HOLD")
        self.assertFalse(step(admission_delay=60001).called)
        self.assertFalse((root / "plan.json").exists())



class SupervisorShutdownTests(unittest.TestCase):
    """Exercise actual run/finally paths with a real registered plan and fake children."""
    def setUp(self):
        self.fixture = fixtures.ShadowTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.study
        self.now = 2000
        self.plan = copy.deepcopy(self.fixture.p)
        self.plan_bytes = (self.root / "plan.json").read_bytes()
        self.predictor = self.root / "predictor-config.json"
        self.predictor.write_text("{}")
        evidence = self.root / "qualification-evidence.json"
        evidence.write_text("{}")
        self.study = {key: self.plan[key] for key in
                      ("producer_registration", "period_sha256", "behavior_model_id",
                       "encoder_backend", "encoder_threads", "encoder_batch_size")}
        self.template = dict(self.study, source_registration=self.study["producer_registration"])
        self.qualification = {"backend": "cpu_fp32", "tensor_cleanup_verified": True,
                              "max_request_seconds": 1, "deadline_budget_seconds": 60,
                              "evidence_path": str(evidence), "evidence_sha256": W.sha(evidence)}
        self.config = {"schema": "heptatrader.weight-shadow-supervisor.v1", "trade_authority": False,
                       "automatic_selection": False, "actor_update": False,
                       "release_manifest": "unused", "release_manifest_sha256": "unused",
                       "study_config_path": "study", "study_config_sha256": "unused",
                       "predictor_template_path": "template", "predictor_template_sha256": "unused",
                       "qualification_path": "qualification", "qualification_sha256": "unused",
                       "state_root": str(self.root), "native_stop_marker": str(self.root / "no-native-stop"),
                       "python": "unused-test-python"}
        self.children = {}

    def run_case(self, *, exit_kind="signal", already_exited=None, cleanup_failure=None, startup_failure=False):
        from contextlib import ExitStack
        from types import SimpleNamespace
        from unittest.mock import Mock
        handlers = {}
        documents = {"study": self.study, "template": self.template, "qualification": self.qualification}
        source = SimpleNamespace(read_successes=Mock(side_effect=AssertionError("registered window must not be gated again")))
        owner = self
        class Child:
            def __init__(self, role):
                self.role = role
                self.pid = 7000 + len(owner.children)
                self.returncode = 0 if role == already_exited else None
                self.terminated = self.killed = False
            def poll(self): return self.returncode
            def terminate(self):
                if self.role == cleanup_failure: raise OSError("synthetic termination failure")
                self.terminated = True
                self.returncode = -15
            def kill(self):
                self.killed = True
                self.returncode = -9
            def wait(self, timeout=None): return self.returncode
        def spawn(command):
            role = command[command.index("--target") + 1]
            child = Child(role)
            self.children[role] = child
            return child
        def sleep(_):
            if exit_kind == "final":
                self.now = self.plan["outcome_end_ms"] + 1
                if not (self.root / "assessment.json").exists():
                    W.atomic(self.root / "assessment.json", E.assessment(self.plan, [], self.now))
                return
            if exit_kind == "exception": raise RuntimeError("synthetic supervisor failure")
            handlers[S.signal.SIGTERM](S.signal.SIGTERM, None)
        with ExitStack() as stack:
            stack.enter_context(patch.object(S, "verify_release", return_value=(S.ROOT, None)))
            stack.enter_context(patch.object(S, "pinned_json", side_effect=lambda path, _: documents[path]))
            stack.enter_context(patch.object(S, "RegistrationSource", return_value=source))
            stack.enter_context(patch.object(S, "original_guard",
                                            side_effect=RuntimeError("synthetic startup failure") if startup_failure else None,
                                            return_value=SimpleNamespace(check=lambda: {"admit_new_worker": True})))
            stack.enter_context(patch.object(S, "ensure_activation", return_value=(self.plan, self.predictor)))
            stack.enter_context(patch.object(S.signal, "signal", side_effect=lambda sig, handler: handlers.update({sig: handler})))
            stack.enter_context(patch.object(S.subprocess, "Popen", side_effect=spawn))
            stack.enter_context(patch.object(S.time, "sleep", side_effect=sleep))
            stack.enter_context(patch.object(W, "clock_ms", side_effect=lambda: self.now))
            return S.run(self.config)

    def status(self):
        self.assertEqual((self.root / "plan.json").read_bytes(), self.plan_bytes)
        return json.loads((self.root / "supervisor-status.json").read_text())

    def test_normal_stop_publishes_empty_children_and_actual_exit_codes(self):
        returned = self.run_case()
        status = self.status()
        self.assertEqual(status, returned)
        self.assertEqual(status["state"], "STOPPED")
        self.assertFalse(status["supervisor_running"])
        self.assertEqual(status["children"], {})
        self.assertEqual({role: value["code"] for role, value in status["child_exits"].items()},
                         {"predictor": -15, "evaluator": -15})
        self.assertTrue(all(child.terminated for child in self.children.values()))

    def test_final_assessment_survives_cleanup_with_no_running_children(self):
        returned = self.run_case(exit_kind="final")
        status = self.status()
        self.assertEqual(status, returned)
        self.assertEqual(status["state"], "FINAL_ASSESSMENT_AVAILABLE")
        self.assertEqual(status["observed_trades"], 0)
        self.assertFalse(status["supervisor_running"])
        self.assertEqual(status["children"], {})
        self.assertEqual(status["child_exits"]["evaluator"]["code"], -15)

    def test_final_unavailable_receipt_survives_cleanup(self):
        self.now = self.plan["outcome_end_ms"] + 1
        terminal = {
            "schema": E.TERMINAL_UNAVAILABLE_SCHEMA, "plan_id": self.plan["plan_id"],
            "state": "FINAL_UNAVAILABLE_NO_SELECTION", "checked_at_ms": self.now,
            "outcome_end_ms": self.plan["outcome_end_ms"], "current_replay_complete": False,
            "observed_trades": None, "original_command_count": None, "prediction_coverage": None,
            "error": {"type": "RuntimeError", "message": "synthetic unavailable source"},
            "last_completed_assessment": None, "last_assessment_read_error": None,
            "financial_candidate_selected": False, "canonical_agentd_accepted": False,
            "trade_authority": False, "broker_orders_sent": 0,
            "reasons": ["FINAL_SOURCE_OR_EVALUATOR_UNAVAILABLE"]}
        path = self.root / "terminal-unavailable.json"
        path.write_text(json.dumps(terminal))
        before = path.read_bytes()
        returned = self.run_case()
        status = self.status()
        self.assertEqual(status, returned)
        self.assertEqual(status["state"], "FINAL_UNAVAILABLE_NO_SELECTION")
        self.assertFalse(status["supervisor_running"])
        self.assertEqual(status["children"], {})
        self.assertEqual(status["child_exits"], {})
        self.assertEqual(self.children, {})
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNone(status["observed_trades"])

    def test_exceptional_exit_stops_children_and_records_failure(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic supervisor failure"):
            self.run_case(exit_kind="exception")
        status = self.status()
        self.assertEqual(status["state"], "FAILED")
        self.assertFalse(status["supervisor_running"])
        self.assertEqual(status["children"], {})
        self.assertEqual(set(status["child_exits"]), {"predictor", "evaluator"})
        self.assertIn("synthetic supervisor failure", status["reason"])

    def test_already_exited_child_keeps_its_actual_exit_code(self):
        self.run_case(already_exited="predictor")
        status = self.status()
        self.assertEqual(status["state"], "STOPPED")
        self.assertEqual(status["child_exits"]["predictor"]["code"], 0)
        self.assertFalse(self.children["predictor"].terminated)
        self.assertEqual(status["children"], {})

    def test_startup_failure_does_not_leave_a_running_status(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic startup failure"):
            self.run_case(startup_failure=True)
        status = self.status()
        self.assertEqual(status["state"], "FAILED")
        self.assertFalse(status["supervisor_running"])
        self.assertEqual(status["children"], {})

    def test_cleanup_failure_does_not_claim_an_unconfirmed_child_exited(self):
        with self.assertRaisesRegex(OSError, "synthetic termination failure"):
            self.run_case(cleanup_failure="predictor")
        status = self.status()
        self.assertEqual(status["state"], "FAILED")
        self.assertFalse(status["supervisor_running"])
        self.assertTrue(status["children"]["predictor"]["running"])
        self.assertNotIn("predictor", status["child_exits"])
        self.assertEqual(status["child_exits"]["evaluator"]["code"], -15)
        self.assertEqual(status["cleanup_errors"][0]["role"], "predictor")


class SupervisorContinuationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = continuation_fixtures.ContinuationTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_registered_continuation_returns_derived_config_without_reactivation(self):
        f = self.fixture
        config = f.pin_record(f.prepare())
        before = {p: p.read_bytes() for p in f.state.glob("*.json")}
        with patch.object(W, "freeze", side_effect=AssertionError("must retain the registered window")):
            plan, predictor = S.ensure_activation(config, {}, {}, f.state)
        self.assertEqual(plan, f.plan)
        self.assertNotEqual(predictor, f.state / "predictor-config.json")
        self.assertEqual(W.read(predictor)["runtime_root"], str(f.newroot))
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_invalid_continuation_does_not_fall_back_to_activation(self):
        f = self.fixture
        config = f.pin_record(f.prepare())
        config["continuation"]["sha256"] = "0" * 64
        with patch.object(W, "freeze", side_effect=AssertionError("no fallback registration")):
            with self.assertRaisesRegex(ValueError, "PIN_CHANGED"):
                S.ensure_activation(config, {}, {}, f.state)
        self.assertFalse((f.state / "continuations").exists())



if __name__ == "__main__": unittest.main()
