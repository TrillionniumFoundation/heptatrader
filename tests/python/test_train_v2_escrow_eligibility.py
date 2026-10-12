"""Only isolated SYNTHETIC eligibility fixtures; no market order or signer."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import unittest

from train_v2.real_source_native_escrow_eligibility import HOLD,assess


class StrictEscrowAdmissionHoldTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/"root"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.plan={
            "behavior_policy_id":"SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1",
            "trade_authority":False,
            "period_sha256":"e"*64,
            "minimum_mature_rows":16,
            "registered_at_ms":100000,
            "first_decision_ms":140000,
            "last_decision_ms":240000,
            "plan_id":"a"*64,
        }
        self.spec={"period_sha256":"e"*64}
        self.write("plan.json",self.plan)
        self.write("feature-spec.json",self.spec)

    def write(self,file,data):
        p=self.root/file
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(data,sort_keys=True)+"\n")
        p.chmod(0o600)

    def test_unconsumed_plan_and_missing_escrow_is_explicit_hold(self):
        r=assess(self.root,now_ms=150000)
        self.assertEqual(r["recovery_and_release_state"],HOLD)
        self.assertFalse(r["plan_consumed_by_new_actual_source_decision"])
        self.assertFalse(r["original_native_decision_signed"])
        self.assertFalse(r["physical_external_escrow_write_once_and_fsync_observed"])
        self.assertTrue(r["plan_window_active_at_check"])

    def test_plan_window_elapsed_does_not_fabricate_decision(self):
        r=assess(self.root,now_ms=250000)
        self.assertFalse(r["plan_window_active_at_check"])
        self.assertEqual(r["recovery_and_release_state"],HOLD)
        self.assertFalse(r["plan_consumed_by_new_actual_source_decision"])

    def test_written_original_native_ack_without_escrow_is_invalid(self):
        self.write("native-lab-v1/native-source-decision-ack.json",{"forged":True})
        with self.assertRaisesRegex(ValueError,"NATIVE_DECISION_WITHOUT_INDEPENDENT_ESCROW"):
            assess(self.root,now_ms=150000)

    def test_written_native_outcome_without_two_escrows_is_invalid(self):
        self.write("native-lab-v1/original-native-decision-independent-escrow.json",{"synthetic":True})
        self.write("native-lab-v1/native-source-decision-ack.json",{"synthetic":True})
        self.write("native-lab-v1/native-price-outcome-and-dataset-test-ack.json",{"synthetic":True})
        with self.assertRaisesRegex(ValueError,"NATIVE_OUTCOME_WITHOUT_INDEPENDENT_ESCROW"):
            assess(self.root,now_ms=150000)

    def test_even_all_self_asserted_files_do_not_grant_product_root(self):
        for name in ("original-native-decision-independent-escrow.json",
                     "original-native-outcome-independent-escrow.json",
                     "native-source-decision-ack.json",
                     "native-price-outcome-and-dataset-test-ack.json"):
            self.write("native-lab-v1/"+name,{"test_only":True})
        x=assess(self.root,now_ms=150000)
        self.assertEqual(x["recovery_and_release_state"],HOLD)
        self.assertFalse(x["hardware_enrolled_external_product_escrow_recovery_authority"])
        self.assertFalse(x["physical_external_escrow_write_once_and_fsync_observed"])

    def test_boolean_plan_clock_refused(self):
        self.plan["registered_at_ms"]=False
        self.write_replace("plan.json",self.plan)
        with self.assertRaisesRegex(ValueError,"ESCROW_PLAN_OR_SOURCE_POLICY_CHANGED"):
            assess(self.root,now_ms=150000)

    def write_replace(self,file,value):
        p=self.root/file
        p.write_text(json.dumps(value,sort_keys=True)+"\n")
        p.chmod(0o600)

    def test_short_preregistration_lead_refused(self):
        self.plan["first_decision_ms"]=self.plan["registered_at_ms"]+29000
        self.write_replace("plan.json",self.plan)
        with self.assertRaisesRegex(ValueError,"ESCROW_PLAN_OR_SOURCE_POLICY_CHANGED"):
            assess(self.root,now_ms=150000)

    def test_outcome_must_have_private_absolute_source_root(self):
        with tempfile.TemporaryDirectory() as other:
            p=Path(other)/"wrong"
            p.symlink_to(self.root)
            with self.assertRaisesRegex(ValueError,"ESCROW_TRIAL_PRIVATE_SCOPE_INVALID"):
                assess(p,now_ms=150000)


if __name__=="__main__":
    unittest.main()
