"""Original PAPER source-only price cohort: immutable 3x4 denominator gates."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from train_v2.contracts import ContractError, digest
from train_v2.price_only_cohort_audit import audit
from train_v2.sealed_outcome_reconcile import classify_source
from test_train_v2_sealed_outcome_reconcile import fixtures, valid_receipt

class CohortAuditTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory()
  self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name)
  self.c,self.f,self.archive=fixtures()
  self.c["created_at_ms"]=self.f["decision_at_ms"]-60000
  self.c["anchors_ms"]=[
   self.f["decision_at_ms"]-1000,
   self.f["decision_at_ms"]+4800000,
   self.f["decision_at_ms"]+9600000]
  self.archive["source_fingerprint_sha256"]="e"*64
  self.f["outcome_maturity_ms"]={
   str(h):self.f["decision_at_ms"]+h*60000 for h in (5,15,30,60)}
  self.roster={"campaign_id":self.c["campaign_id"],"roster_id":"f"*64}
  for name in ("campaign.json","timers-roster.json",
               "slot-00.input.json","slot-00.timer-result.json"):
   p=self.root/name;p.write_text("{}");p.chmod(0o600)
  self.now=self.f["outcome_maturity_ms"]["5"]+5100
  self.patches=[
   patch("train_v2.price_only_cohort_audit._load",return_value=self.c),
   patch("train_v2.price_only_cohort_audit.load_roster",return_value=self.roster),
   patch("train_v2.price_only_cohort_audit.verify_input_and_timer",
         return_value=(self.f,{"slot":0})),
  ]
  for patcher in self.patches:
   patcher.start();self.addCleanup(patcher.stop)

 def runit(self,now=None):
  return audit(self.root,now_ms=self.now if now is None else now,
               archive_loader=lambda *_:self.archive)

 def seal_h5(self):
  state,expected=classify_source(self.c,self.f,0,5,self.archive)
  self.assertEqual(state,"READY_ORIGINAL_SEALED_PRICE_ONLY")
  item=valid_receipt(expected,self.archive)
  path=self.root/"slot-00-h05.outcome.json"
  path.write_text(json.dumps(item,sort_keys=True));path.chmod(0o600)
  return item

 def test_frozen_twelve_slots_and_prices_only(self):
  self.seal_h5()
  out=self.runit()
  self.assertEqual(out["declared_source_horizon_cells"],12)
  self.assertEqual(len(out["all_slots"]),3)
  self.assertEqual(out["state_counts"]["VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY"],1)
  self.assertEqual(out["state_counts"]["PENDING_NOT_MATURE"],3)
  self.assertEqual(out["state_counts"]["NO_SOURCE_INPUT_NO_PRICE_OUTCOME"],8)
  self.assertFalse(out["financial_alpha_qualified"])
  self.assertEqual(out["broker_orders_sent"],0)
  self.assertEqual(out["original_model_forecast_count"],0)

 def test_existing_observed_price_does_not_become_strategy_profit(self):
  self.seal_h5()
  item=self.runit()["all_slots"][0]["outcomes"][0]
  self.assertEqual(item["state"],"VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY")
  self.assertIsNotNone(item["price_bps"])
  self.assertFalse(item["economically_qualified"])
  self.assertTrue(item["observed_price_below_stress_hurdle"])

 def test_mature_price_without_original_outcome_remains_unsealed(self):
  item=self.runit()["all_slots"][0]["outcomes"][0]
  self.assertEqual(item["state"],"READY_ONLY_NOT_YET_SEALED")
  self.assertIsNone(item["price_bps"])
  self.assertFalse((self.root/"slot-00-h05.outcome.json").exists())

 def test_future_slot_missing_input_does_not_imply_market_no_quote(self):
  out=self.runit()
  for row in out["all_slots"][1:]:
   self.assertEqual(row["slot_state"],"FUTURE_OR_OPEN_SOURCE_SLOT")
   self.assertTrue(all(x["price_bps"] is None for x in row["outcomes"]))

 def test_missed_capture_cannot_be_scored(self):
  now=self.c["anchors_ms"][2]+17000
  out=self.runit(now)
  self.assertEqual(out["all_slots"][1]["slot_state"],
                   "MISSED_NO_CAPTURE_NOT_A_PRICE_OR_ZERO_PNL")
  self.assertEqual(out["all_slots"][2]["slot_state"],
                   "MISSED_NO_CAPTURE_NOT_A_PRICE_OR_ZERO_PNL")

 def test_missing_timer_is_hard_error_not_missed_source(self):
  (self.root/"slot-00.timer-result.json").unlink()
  with self.assertRaisesRegex(ContractError,"COHORT_AUDIT_PARTIAL_CAPTURE"):
   self.runit()

 def test_symlinked_original_input_refused(self):
  p=self.root/"slot-00.input.json";p.unlink();p.symlink_to(self.root/"campaign.json")
  with self.assertRaisesRegex(ContractError,"COHORT_AUDIT_SYMLINK_INPUT"):
   self.runit()

 def test_tampered_rehashed_observed_price_does_not_pass(self):
  original=self.seal_h5()
  mutated=dict(original);mutated["original_midprice_change_bps"]="10000"
  mutated["result_id"]=digest({k:v for k,v in mutated.items() if k!="result_id"})
  (self.root/"slot-00-h05.outcome.json").write_text(json.dumps(mutated))
  with self.assertRaises(ContractError):
   self.runit()

 def test_rehashed_future_dated_price_receipt_not_in_past_audit(self):
  original=self.seal_h5()
  altered=dict(original)
  altered["observed_at_ms"]=self.now+60000
  altered["result_id"]=digest({k:v for k,v in altered.items()
                                if k!="result_id"})
  (self.root/"slot-00-h05.outcome.json").write_text(
   json.dumps(altered,sort_keys=True))
  with self.assertRaisesRegex(
      ContractError,"COHORT_AUDIT_OUTCOME_OBSERVED_IN_FUTURE"):
   self.runit()

 def test_future_maturity_outcome_receipt_cannot_exist(self):
  self.seal_h5()
  now=self.f["outcome_maturity_ms"]["5"]+10
  with self.assertRaisesRegex(ContractError,"COHORT_AUDIT_OUTCOME_BEFORE_MATURITY"):
   self.runit(now)

 def test_unsealed_original_endpoint_keeps_price_unknown(self):
  trimmed={**self.archive,"rows":self.archive["rows"][:34]}
  out=audit(self.root,now_ms=self.now,archive_loader=lambda *_:trimmed)
  self.assertEqual(out["all_slots"][0]["outcomes"][0]["state"],
                   "WAIT_ORIGINAL_SEALED_ENDPOINT_COVERAGE")
  self.assertIsNone(out["all_slots"][0]["outcomes"][0]["price_bps"])

 def test_fabricated_large_price_move_still_cannot_qualify_alpha(self):
  from decimal import Decimal
  # Pure TEST fixture: very large hindsight price move has no signed model,
  # executable fill, verified slippage or forecast action.
  due=self.f["outcome_maturity_ms"]["5"]
  modified=[]
  for record in self.archive["rows"]:
   value=(record[4]*Decimal("1.02")
          if record[1]>=due else record[4])
   modified.append((*record[:4],value))
  self.archive["rows"]=modified
  state,expected=classify_source(self.c,self.f,0,5,self.archive)
  self.assertEqual(state,"READY_ORIGINAL_SEALED_PRICE_ONLY")
  from train_v2.original_multihorizon import HORIZONS_MIN
  self.assertIn(5,HORIZONS_MIN)
  receipt=valid_receipt(expected,self.archive)
  f=self.root/"slot-00-h05.outcome.json"
  f.write_text(json.dumps(receipt));f.chmod(0o600)
  cell=self.runit()["all_slots"][0]["outcomes"][0]
  self.assertEqual(cell["state"],"VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY")
  self.assertFalse(cell["observed_price_below_stress_hurdle"])
  self.assertFalse(cell["economically_qualified"])

 def test_censored_original_path_cannot_be_zero_price_profit(self):
  due=self.f["outcome_maturity_ms"]["5"]
  removed=[q for q in self.archive["rows"]
           if not (due-45000<=q[1]<=due-10000)]
  self.archive["rows"]=removed
  state,expected=classify_source(self.c,self.f,0,5,self.archive)
  self.assertEqual(state,"READY_ORIGINAL_SEALED_PRICE_ONLY")
  self.assertTrue(expected["outcome_state"].startswith("CENSORED_"))
  receipt=valid_receipt(expected,self.archive)
  f=self.root/"slot-00-h05.outcome.json"
  f.write_text(json.dumps(receipt));f.chmod(0o600)
  cell=self.runit()["all_slots"][0]["outcomes"][0]
  self.assertEqual(cell["state"],"VERIFIED_CENSORED_ORIGINAL_PRICE_ONLY")
  self.assertIsNone(cell["price_bps"])
  self.assertFalse(cell["economically_qualified"])

 def test_existing_result_against_now_missing_sealed_input_denied(self):
  self.seal_h5()
  copy={**self.archive,"rows":[r for r in self.archive["rows"]
        if r[3]!=self.f["original_source_id"]]}
  with self.assertRaisesRegex(ContractError,"COHORT_AUDIT_SOURCE_SEAL_MISSING"):
   audit(self.root,now_ms=self.now,archive_loader=lambda *_:copy)

 def test_record_digest_idempotent_over_identical_source(self):
  self.seal_h5()
  a=self.runit();b=self.runit()
  self.assertEqual(a,b)
  self.assertEqual(a["audit_id"],digest({k:v for k,v in a.items()
                                        if k!="audit_id"}))
