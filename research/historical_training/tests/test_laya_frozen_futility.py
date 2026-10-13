"""Immutable original Laya 89-slot futility: PENDING != eligible."""
import copy
import tempfile
import unittest
from pathlib import Path
from historical_training.common import canonical,sha
from historical_training.laya_frozen_futility import assess_original_futility
from laya_cell.integrity import ContractError,digest
from laya_cell.promotion import rule_for_trial
from laya_cell.promotion_power import PROTOCOL

class FrozenFutilityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)
  self.plan={
   "schema":"heptatrader.laya-resident-future-plan.v1",
   "candidate_id":"frozen-original-candidate",
   "trial":31,
   "rule":rule_for_trial(31,PROTOCOL),
  }
  self.plan["plan_id"]=digest(self.plan)
  self.planfile=self.root/"plan.json"
  self.planfile.write_bytes(canonical(self.plan)+b"\n")
  self.assertEqual(self.plan["rule"]["slots"],89)
  self.assertEqual(self.plan["rule"]["min_observed"],87)
  self.assertEqual(self.plan["rule"]["max_censored"],2)

 def outcome(self,slot,status="CENSORED"):
  obj={"plan_id":self.plan["plan_id"],
       "candidate_id":self.plan["candidate_id"],"slot":slot,
       "status":status}
  p=self.root/f"outcome-{slot:03}.json"
  p.write_bytes(canonical(obj)+b"\n")
  return p

 def test_three_original_censors_make_89_slot_trial_irreversible(self):
  for i in range(3):self.outcome(i)
  report=assess_original_futility(self.planfile)
  self.assertEqual(report["precommitted_slots"],89)
  self.assertEqual(report["verified_terminal_censored"],3)
  self.assertEqual(report["maximum_possible_observed_after_current_censoring"],86)
  self.assertTrue(report["statistical_qualification_already_mathematically_impossible"])
  self.assertEqual(report["audit_status"],
                   "FROZEN_PROMOTION_IMPOSSIBLE_NO_RETROACTIVE_REPAIR")
  self.assertFalse(report["automatic_model_promotion"])
  self.assertFalse(report["original_evaluator_qualification_rewritten"])
  self.assertFalse(report["economic_alpha_qualified"])
  self.assertEqual(report["broker_orders_sent"],0)

 def test_two_censors_do_not_falsely_claim_irreversible_futility(self):
  self.outcome(0);self.outcome(1)
  report=assess_original_futility(self.planfile)
  self.assertFalse(report["statistical_qualification_already_mathematically_impossible"])
  self.assertEqual(report["maximum_possible_observed_after_current_censoring"],87)
  self.assertEqual(report["missing_or_future_slots_not_mislabeled_as_zero"],87)

 def test_partial_observed_never_counts_as_independent_trade_rewards(self):
  self.outcome(0,"OBSERVED");self.outcome(1,"CENSORED")
  r=assess_original_futility(self.planfile)
  self.assertEqual(r["verified_terminal_observed"],1)
  self.assertEqual(r["verified_terminal_censored"],1)
  self.assertFalse(r["economic_alpha_qualified"])

 def test_rehashed_wrong_original_plan_id_is_rejected(self):
  p=copy.deepcopy(self.plan)
  p["candidate_id"]="other"
  self.planfile.write_bytes(canonical(p)+b"\n")
  with self.assertRaisesRegex(ContractError,"FUTILITY_PLAN_MUTATED"):
   assess_original_futility(self.planfile)

 def test_outcome_wrong_candidate_or_slot_rejected(self):
  o=self.outcome(0)
  o.write_bytes(canonical({"plan_id":self.plan["plan_id"],
       "candidate_id":"foreign","slot":0,"status":"CENSORED"})+b"\n")
  with self.assertRaisesRegex(ContractError,"FUTILITY_ORIGINAL_OUTCOME_IDENTITY"):
   assess_original_futility(self.planfile)

 def test_unknown_state_is_not_zero_or_discarded(self):
  self.outcome(0,"PENDING")
  with self.assertRaisesRegex(ContractError,"FUTILITY_UNRECOGNIZED_ORIGINAL_OUTCOME"):
   assess_original_futility(self.planfile)

 def test_outcome_symlink_does_not_count_as_original(self):
  f=self.root/"outside";f.write_text("{}")
  (self.root/"outcome-000.json").symlink_to(f)
  with self.assertRaisesRegex(ContractError,"FUTILITY_OUTCOME_SYMLINK"):
   assess_original_futility(self.planfile)

 def test_output_files_are_not_touched_during_readonly_audit(self):
  o=self.outcome(0)
  before={str(p):sha(p) for p in (self.planfile,o)}
  assess_original_futility(self.planfile)
  after={str(p):sha(p) for p in (self.planfile,o)}
  self.assertEqual(before,after)

if __name__=="__main__":unittest.main()
