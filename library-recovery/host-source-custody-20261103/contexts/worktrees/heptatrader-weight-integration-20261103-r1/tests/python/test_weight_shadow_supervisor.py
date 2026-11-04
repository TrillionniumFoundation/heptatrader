import copy
import unittest
from unittest.mock import patch
import test_current100k_weight_shadow as fixtures
import supervise_current100k_weight_shadow as S
from laya_cell import current100k_weight_shadow as W

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

if __name__ == "__main__": unittest.main()
