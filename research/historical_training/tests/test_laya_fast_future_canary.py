"""Fail-closed prospective source-only canary: no model, no orders."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_training.common import sha
from historical_training.laya_fast_future_canary import (
    MIN_LEAD_MS, WINDOW_MS, register, capture, verify,
)
from laya_cell.integrity import ContractError

BASE=1791865000000


class FutureOriginalCanaryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)/"research"
  self.root.mkdir()
  self.period=self.root/"period.json"
  self.period.write_text('{"execution_domain":"PAPER"}\n')
  self.feed=self.root/"feed"
  self.feed.mkdir()
  self.config=self.root/"original-config.json"
  self.conf={
    "period_path":str(self.period),
    "period_sha256":sha(self.period),
    "feed_directory":str(self.feed),
    "completed_history_sources":[],
  }
  self.write_config()
  self.out=self.root/"future-1"
  self.slot=BASE+120000
  self.quote={"t":self.slot+1,"known_at_ms":self.slot+2,
              "receipt":"f"*64,"bid":1.1,"ask":1.1001}
  self.sample={"sample_id":"sample-1","anchor_ms":self.slot+1,
               "history_selection_version":"CONTIGUOUS_SELECTED_SUFFIX_V2"}
  self.row={"state":{"sample":"fixed-asof","anchor":self.slot+1}}

 def write_config(self):
  self.config.write_text(json.dumps(self.conf)+"\n")

 def register(self):
  with patch("historical_training.laya_fast_future_canary.now_ms",return_value=BASE):
   return register(self.out,self.config,self.slot)

 def source(self,*,known=None,anchor=None,delay=250,commit_time=None):
  q=dict(self.quote)
  if known is not None:q["known_at_ms"]=known
  sample=dict(self.sample)
  if anchor is not None:sample["anchor_ms"]=anchor
  # patch original reader and feature construction for isolated clock tests;
  # only the separate real-host proof runs unmodified original files.
  reader=patch("historical_training.laya_fast_future_canary.read_recent_original_quotes",
               return_value=([q],[{"source":"immutable-pinned"}]))
  forecast=patch("historical_training.laya_fast_future_canary.forecast_input",
                 return_value=(sample,self.row))
  full=patch("historical_training.laya_fast_future_canary.read_quotes",
             return_value=([q],[{"source":"original"}]))
  times=patch("historical_training.laya_fast_future_canary.now_ms",
              side_effect=[self.slot+2,self.slot+2,self.slot+3,
                           self.slot+delay,(self.slot+delay+50 if commit_time is None else self.slot+commit_time)])
  return reader,forecast,full,times

 def run_capture(self,**kwargs):
  patches=self.source(**kwargs)
  with patches[0],patches[1],patches[2],patches[3]:
   return capture(self.out,self.config)

 def test_one_new_original_source_after_genuine_preregistration(self):
  plan=self.register()
  self.assertEqual(plan["window_end_ms"],self.slot+WINDOW_MS)
  self.assertGreaterEqual(self.slot-plan["registered_at_ms"],MIN_LEAD_MS)
  ack=self.run_capture()
  self.assertTrue(ack["source_input_sealed_before_deadline"])
  self.assertEqual(ack["broker_orders_sent"],0)
  self.assertFalse(ack["model_loaded"])
  with patch("historical_training.laya_fast_future_canary.read_quotes",
             return_value=([self.quote],[])),patch(
              "historical_training.laya_fast_future_canary.forecast_input",
              return_value=(self.sample,self.row)):
   result=verify(self.out,self.config)
  self.assertTrue(result["complete_original_asof_feature_parity"])
  self.assertEqual(result["source_decision_population_count"],1)
  self.assertEqual(result["model_predictions"],0)

 def test_duplicate_preregistration_never_overwrites_existing_plan(self):
  self.register()
  with patch("historical_training.laya_fast_future_canary.now_ms",return_value=BASE):
   with self.assertRaises(FileExistsError):
    register(self.out,self.config,self.slot)

 def test_late_registration_does_not_relabel_historical_source(self):
  with patch("historical_training.laya_fast_future_canary.now_ms",
             return_value=self.slot-MIN_LEAD_MS+1):
   with self.assertRaisesRegex(ContractError,"CANARY_MUST_REGISTER_BEFORE_FUTURE"):
    register(self.out,self.config,self.slot)
  self.assertFalse(self.out.exists())

 def test_capture_not_due_refuses_without_writing(self):
  self.register()
  with patch("historical_training.laya_fast_future_canary.now_ms",
             return_value=self.slot-2):
   with self.assertRaisesRegex(ContractError,"CANARY_FUTURE_SLOT_NOT_DUE"):
    capture(self.out,self.config)
  self.assertFalse((self.out/"input.json").exists())

 def test_original_event_before_registration_fails(self):
  self.register()
  q=self.source(known=BASE-1)
  with q[0],q[1],q[2],patch(
     "historical_training.laya_fast_future_canary.now_ms",
     side_effect=[self.slot+2,self.slot+2,self.slot+3,
                  self.slot+250,self.slot+17000,self.slot+17000]):
   with patch("historical_training.laya_fast_future_canary.time.sleep"):
    with self.assertRaises(ContractError):
     capture(self.out,self.config)
  self.assertFalse((self.out/"input.json").exists())

 def test_slot_quote_before_declared_window_cannot_enter(self):
  self.register()
  q=self.source(anchor=self.slot-1)
  with q[0],q[1],q[2],patch(
     "historical_training.laya_fast_future_canary.now_ms",
     side_effect=[self.slot+2,self.slot+2,self.slot+3,
                  self.slot+250,self.slot+17000,self.slot+17000]):
   with patch("historical_training.laya_fast_future_canary.time.sleep"):
    with self.assertRaises(ContractError):
     capture(self.out,self.config)

 def test_final_source_commit_after_deadline_fails(self):
  self.register()
  ack=self.run_capture(delay=250,commit_time=WINDOW_MS+2)
  self.assertFalse(ack["source_input_sealed_before_deadline"])
  with self.assertRaisesRegex(ContractError,"CANARY_CAPTURE_ACK_OR_IDENTITY_NOT_VALID"):
   verify(self.out,self.config)

 def test_replayed_second_capture_denied(self):
  self.register();self.run_capture()
  with self.assertRaisesRegex(ContractError,"CANARY_CAPTURE_ALREADY_ATTEMPTED"):
   capture(self.out,self.config)

 def test_future_hash_changes_fail_before_source_read(self):
  self.register()
  with patch("historical_training.laya_fast_future_canary.file_inventory",
             return_value={"forged":"0"*64}):
   with self.assertRaisesRegex(ContractError,"CANARY_PINNED_CODE_CHANGED"):
    capture(self.out,self.config)

 def test_original_config_changed_after_registration_is_rejected(self):
  self.register()
  self.conf["new_unreviewed_reader"]=True
  self.write_config()
  with self.assertRaisesRegex(ContractError,"CANARY_SOURCE_SCOPE_CHANGED"):
   capture(self.out,self.config)

 def test_source_selected_different_after_independent_full_read_is_rejected(self):
  self.register();self.run_capture()
  changed=dict(self.sample);changed["sample_id"]="different"
  with patch("historical_training.laya_fast_future_canary.read_quotes",
             return_value=([self.quote],[])),patch(
      "historical_training.laya_fast_future_canary.forecast_input",
      return_value=(changed,self.row)):
   with self.assertRaisesRegex(ContractError,"CANARY_INDEPENDENT_FULL_SOURCE_PARITY_FAILURE"):
    verify(self.out,self.config)

 def test_rehashed_or_tampered_source_commit_rejected(self):
  self.register();self.run_capture()
  path=self.out/"commit.json"
  obj=json.loads(path.read_text());obj["model_loaded"]=True
  path.chmod(0o600)
  path.write_text(json.dumps(obj))
  with self.assertRaisesRegex(ContractError,"CANARY_CAPTURE_ACK_OR_IDENTITY_NOT_VALID"):
   verify(self.out,self.config)

 def test_register_does_not_change_parent_python_process_umask(self):
  import os
  original=os.umask(0o027)
  try:
   self.register()
   current=os.umask(0o027)
   self.assertEqual(current,0o027)
  finally:
   os.umask(original)

 def test_registered_source_is_price_only_without_model_or_owner(self):
  plan=self.register()
  self.assertIs(plan["model_inference_allowed"],False)
  self.assertIs(plan["canonical_owner_enrolled"],False)
  self.assertIs(plan["automatic_model_promotion"],False)
  self.assertIs(plan["original_account_paper_only"],True)


if __name__=="__main__":
 unittest.main()
