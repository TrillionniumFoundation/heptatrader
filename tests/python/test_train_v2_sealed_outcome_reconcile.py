"""Finite price-only original outcome reconciliation, no broker or model."""
import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from train_v2.contracts import ContractError, digest
from train_v2.sealed_outcome_reconcile import (
    classify_source, verify_immutable_outcome, reconcile,
)
from train_v2.prospective_horizon_observe import (
    MIN_OUTCOME_GRACE_MS, OUTCOME_SCHEMA,
)

BASE = 1800000000000

def fixtures():
    quotes=[]
    for i in range(125):
        event=BASE+i*5000
        quotes.append((event+100, event, event+4900,
                       ("%064x"%(i+1)), Decimal("1.100000")+
                       Decimal(i)*Decimal("0.000001")))
    original=quotes[6]
    decision=original[0]+200
    cid="a"*64
    campaign={"campaign_id":cid, "anchors_ms":[BASE+60000,
        BASE+60000+4800000,BASE+60000+9600000],
        "source_root":"/mock/original","period_sha256":"b"*64}
    frozen={"campaign_id":cid,"slot":0,"input_id":"c"*64,
            "original_source_id":original[3],
            "source_available_at_ms":original[0],
            "source_event_at_ms":original[1],
            "source_valid_until_ms":original[2],
            "input_quote_midpoint":str(original[4]),
            "decision_at_ms":decision,
            "outcome_maturity_ms":{"5":decision+300000}}
    archive={"rows":quotes,"sealed_segments":3}
    return campaign,frozen,archive


def valid_receipt(expected,archive):
    due=expected["due_at_ms"]
    body={**expected,"observed_at_ms":due+5001,
          "original_scanned_source_fingerprint_sha256":"f"*64,
          "sealed_source_segment_count":2}
    return {**body,"result_id":digest(body)}


class ReconcilePureTests(unittest.TestCase):
 def setUp(self):
  self.c,self.f,self.a=fixtures()
  state,self.expected=classify_source(self.c,self.f,0,5,self.a)
  self.assertEqual(state,"READY_ORIGINAL_SEALED_PRICE_ONLY")
  self.assertEqual(self.expected["outcome_state"],
                   "OBSERVED_NEW_ORIGINAL_MARKET_PRICE_ONLY")
  self.receipt=valid_receipt(self.expected,self.a)

 def verify(self,item=None):
  return verify_immutable_outcome(
   self.receipt if item is None else item,self.expected,
   due=self.f["outcome_maturity_ms"]["5"],archive=self.a)

 def test_original_price_observed_only_with_complete_contiguous_source(self):
  self.assertEqual(self.verify(),"VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY")
  self.assertFalse(self.receipt["trading_authority"])
  self.assertFalse(self.receipt["independent_observer_signed"])

 def test_latest_original_segment_extension_keeps_old_seal_valid(self):
  after=dict(self.a, sealed_segments=6)
  self.assertEqual(verify_immutable_outcome(
   self.receipt,self.expected,due=self.f["outcome_maturity_ms"]["5"],
   archive=after),"VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY")

 def test_original_input_not_yet_archived_stays_wait(self):
  archive={"rows":[r for r in self.a["rows"]
                   if r[3]!=self.f["original_source_id"]],
           "sealed_segments":3}
  state,result=classify_source(self.c,self.f,0,5,archive)
  self.assertEqual(state,"WAIT_INPUT_ORIGINAL_SEGMENT_SEAL")
  self.assertIsNone(result)

 def test_original_endpoint_not_fully_archived_stays_wait(self):
  archive={"rows":self.a["rows"][:33],"sealed_segments":1}
  state,result=classify_source(self.c,self.f,0,5,archive)
  self.assertEqual(state,"WAIT_END_OF_ORIGINAL_SEALED_COVERAGE")
  self.assertIsNone(result)

 def test_bounded_original_gap_censors_not_zero_pnl(self):
  archive={"rows":self.a["rows"][:23]+self.a["rows"][29:],
           "sealed_segments":3}
  state,result=classify_source(self.c,self.f,0,5,archive)
  self.assertEqual(state,"READY_ORIGINAL_SEALED_PRICE_ONLY")
  self.assertEqual(result["outcome_state"],
                   "CENSORED_INTERVENING_SOURCE_GAP")
  self.assertIsNone(result["original_midprice_change_bps"])
  self.assertEqual(result["no_trade_reference_net_pnl_usd"],"0")

 def test_rehashed_price_tamper_is_rejected(self):
  bad=dict(self.receipt)
  bad["original_midprice_change_bps"]="99999"
  bad["result_id"]=digest({k:v for k,v in bad.items()
                           if k!="result_id"})
  with self.assertRaisesRegex(ContractError,
                       "OUTCOME_RECONCILE_PREVIOUS_PRICE_OR_SCOPE_CHANGED"):
   self.verify(bad)

 def test_rehashed_false_model_and_owner_fields_rejected(self):
  for key in ("independent_observer_signed","trading_authority",
              "canonical_owner_enrolled","model_selection_permitted"):
   bad=dict(self.receipt);bad[key]=True
   bad["result_id"]=digest({k:v for k,v in bad.items()
                            if k!="result_id"})
   with self.subTest(key=key),self.assertRaises(ContractError):
    self.verify(bad)

 def test_result_digest_tamper_rejected(self):
  changed=dict(self.receipt)
  changed["result_id"]="0"*64
  with self.assertRaisesRegex(ContractError,
                            "OUTCOME_RECONCILE_EXISTING_RECEIPT_INVALID"):
   self.verify(changed)

 def test_original_maturity_never_backdated(self):
  changed=dict(self.receipt)
  changed["observed_at_ms"]=self.f["outcome_maturity_ms"]["5"]+2000
  changed["result_id"]=digest({k:v for k,v in changed.items()
                               if k!="result_id"})
  with self.assertRaisesRegex(ContractError,
                            "OUTCOME_RECONCILE_EXISTING_RECEIPT_INVALID"):
   self.verify(changed)

 def test_invalid_original_source_input_quote_is_not_an_outcome(self):
  changed=copy.deepcopy(self.f)
  changed["input_quote_midpoint"]="1.9"
  with self.assertRaisesRegex(ContractError,"PROSPECTIVE_OUTCOME_ORIGINAL_INPUT_CHANGED"):
   classify_source(self.c,changed,0,5,self.a)

class ReconcileDriverTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)
  self.c,self.f,self.archive=fixtures()
  self.roster={"campaign_id":self.c["campaign_id"],"roster_id":"c"*64}
  for name in ("slot-00.input.json","slot-00.timer-result.json"):
   (self.root/name).write_text("{}")
  self.due=self.f["outcome_maturity_ms"]["5"]
  self.patches=[
   patch("train_v2.sealed_outcome_reconcile._load",return_value=self.c),
   patch("train_v2.sealed_outcome_reconcile.load_roster",
         return_value=self.roster),
   patch("train_v2.sealed_outcome_reconcile.verify_input_and_timer",
         return_value=(self.f,{"slot":0})),
   patch("train_v2.sealed_outcome_reconcile.load_original_quote_archive",
         return_value=self.archive),
  ]
  for m in self.patches:
   m.start()
   self.addCleanup(m.stop)

 def target(self):
  return self.root/"slot-00-h05.outcome.json"

 def runit(self,*,commit=False,at=None):
  return reconcile(self.root,0,5,commit=commit,now_ms=at or self.due+5100)

 def save_valid(self):
  _,expected=classify_source(self.c,self.f,0,5,self.archive)
  receipt=valid_receipt(expected,self.archive)
  self.target().write_text(json.dumps(receipt,sort_keys=True))
  self.target().chmod(0o600)
  return receipt

 def test_no_retroactive_missed_input_becomes_zero_outcome(self):
  (self.root/"slot-00.input.json").unlink()
  (self.root/"slot-00.timer-result.json").unlink()
  x=self.runit(commit=True)
  self.assertEqual(x["state"],"MISSED_INPUT_NOT_A_PRICE_OBSERVATION")
  self.assertFalse(self.target().exists())
  self.assertEqual(x["broker_orders_sent"],0)

 def test_missing_timer_with_existing_input_is_not_silent_miss(self):
  (self.root/"slot-00.timer-result.json").unlink()
  with self.assertRaisesRegex(
      ContractError,"OUTCOME_RECONCILE_PARTIAL_SOURCE_OR_TIMER_RECEIPT"):
   self.runit(commit=True)
  self.assertFalse(self.target().exists())

 def test_symlink_source_even_when_missing_is_not_silent_miss(self):
  (self.root/"slot-00.input.json").unlink()
  (self.root/"slot-00.input.json").symlink_to(self.root/"missing-source")
  with self.assertRaisesRegex(
      ContractError,"OUTCOME_RECONCILE_SOURCE_OR_TIMER_SYMLINK"):
   self.runit(commit=True)

 def test_future_horizon_cannot_force_a_label(self):
  with patch("train_v2.sealed_outcome_reconcile.load_original_quote_archive") as reader:
   x=self.runit(commit=True,at=self.due+2000)
   self.assertEqual(x["state"],"WAIT_ORIGINAL_MATURITY")
   self.assertFalse(x["outcome_written"])
   reader.assert_not_called()
  self.assertFalse(self.target().exists())

 def test_unsealed_input_segment_waits_without_output(self):
  archive=dict(self.archive,
               rows=[r for r in self.archive["rows"]
                     if r[3]!=self.f["original_source_id"]])
  with patch("train_v2.sealed_outcome_reconcile.load_original_quote_archive",
             return_value=archive):
   x=self.runit(commit=True)
   self.assertEqual(x["state"],"WAIT_INPUT_ORIGINAL_SEGMENT_SEAL")
  self.assertFalse(self.target().exists())

 def test_covered_original_unsealed_endpoint_waits(self):
  archive=dict(self.archive,rows=self.archive["rows"][:31])
  with patch("train_v2.sealed_outcome_reconcile.load_original_quote_archive",
             return_value=archive):
   x=self.runit(commit=True)
   self.assertEqual(x["state"],"WAIT_END_OF_ORIGINAL_SEALED_COVERAGE")
  self.assertFalse(self.target().exists())

 def test_ready_dryrun_never_writes(self):
  with patch("train_v2.sealed_outcome_reconcile.observe") as original:
   x=self.runit()
   self.assertEqual(x["state"],"READY_ONLY_NO_WRITE")
   original.assert_not_called()
  self.assertFalse(self.target().exists())

 def test_rehashed_future_dated_original_receipt_refused_at_asof(self):
  original=self.save_valid()
  changed=dict(original)
  changed["observed_at_ms"]=self.due+70000
  changed["result_id"]=digest({k:v for k,v in changed.items()
                                if k!="result_id"})
  self.target().write_text(json.dumps(changed,sort_keys=True))
  with self.assertRaisesRegex(
      ContractError,"OUTCOME_RECONCILE_RECEIPT_FROM_FUTURE"):
   self.runit(at=self.due+5100)

 def test_one_original_observer_commit_then_independent_idempotent_read(self):
  def seal(*args,**kwargs):
   self.save_valid()
   return {"outcome_state":"OBSERVED_NEW_ORIGINAL_MARKET_PRICE_ONLY"}
  with patch("train_v2.sealed_outcome_reconcile.observe",side_effect=seal) as run:
   first=self.runit(commit=True)
   self.assertTrue(first["outcome_written"])
   self.assertEqual(first["state"],"VERIFIED_OBSERVED_ORIGINAL_PRICE_ONLY")
   run.assert_called_once()
  with patch("train_v2.sealed_outcome_reconcile.observe") as run:
   repeated=self.runit(commit=True)
   self.assertEqual(repeated["state"],first["state"])
   self.assertFalse(repeated["outcome_written"])
   self.assertEqual(repeated["previous_outcome_sha256"],first["outcome_sha256"])
   run.assert_not_called()

 def test_original_observer_refuses_missing_durable_file(self):
  with patch("train_v2.sealed_outcome_reconcile.observe",
             return_value={"state":"UNKNOWN"}) as original:
   with self.assertRaisesRegex(ContractError,
        "OUTCOME_RECONCILE_ORIGINAL_OBSERVER_NOT_COMMITTED"):
    self.runit(commit=True)
   original.assert_called_once()

 def test_existing_outcome_without_original_quote_is_not_success(self):
  self.save_valid()
  archive=dict(self.archive,
               rows=[r for r in self.archive["rows"]
                     if r[3]!=self.f["original_source_id"]])
  with patch("train_v2.sealed_outcome_reconcile.load_original_quote_archive",
             return_value=archive):
   with self.assertRaisesRegex(ContractError,
      "OUTCOME_RECONCILE_EXISTING_WITHOUT_ORIGINAL_SOURCE"):
    self.runit()

 def test_old_result_with_falsified_return_cannot_be_recovered(self):
  receipt=self.save_valid()
  receipt["original_midprice_change_bps"]="12345"
  receipt["result_id"]=digest({k:v for k,v in receipt.items()
                               if k!="result_id"})
  self.target().write_text(json.dumps(receipt))
  self.target().chmod(0o600)
  with self.assertRaisesRegex(ContractError,
        "OUTCOME_RECONCILE_PREVIOUS_PRICE_OR_SCOPE_CHANGED"):
   self.runit()
