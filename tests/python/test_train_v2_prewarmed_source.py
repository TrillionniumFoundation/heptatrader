"""Test-only prospective source and physical signer fail-closed edge tests."""
import hashlib,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from train_v2.contracts import ContractError
from train_v2.prewarmed_source_worker import (
 latest_original_segment,private_write,DOMAIN,WINDOW_LEAD_MS,
 DECISION_DELAY_MS,ORIGINAL_SEGMENT_NAME
)
from train_v2.prewarmed_signer_latency import percentile,DOMAIN as SIGN_DOMAIN

class SegmentSelectionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)
  self.now=1791789000000000000
 def mk(self,name):
  p=self.root/name/"quotes.jsonl";p.parent.mkdir();p.write_bytes(b"test\n");return p
 def test_old_lexicographic_recovery_segment_cannot_mask_live(self):
  old=self.mk("segment-recovery-live")
  a=self.mk("segment-1791788845839928307-df681cb9")
  b=self.mk("segment-1791788846839928307-a007cacc")
  self.assertEqual(sorted((old,a,b))[-1],old)
  self.assertEqual(latest_original_segment(self.root,now_ns=self.now),b)
 def test_future_numeric_segment_fails_closed(self):
  self.mk("segment-1791799000000000000-abcdef12")
  with self.assertRaisesRegex(ContractError,"ORIGINAL_SEGMENT_START_IN_FUTURE"):
   latest_original_segment(self.root,now_ns=self.now)
 def test_symlink_live_segment_denied(self):
  real=self.mk("segment-1791788845839928307-df681cb9")
  real.unlink()
  real.symlink_to(self.root/"nonexistent")
  with self.assertRaisesRegex(ContractError,"ORIGINAL_LIVE_SEGMENT_SYMLINK"):
   latest_original_segment(self.root,now_ns=self.now)
 def test_only_recovery_segment_provides_no_live_source(self):
  self.mk("segment-recovery-live")
  self.assertIsNone(latest_original_segment(self.root,now_ns=self.now))
 def test_duplicate_writer_segment_start_denied(self):
  self.mk("segment-1791788845839928307-aaaaaaaa")
  self.mk("segment-1791788845839928307-bbbbbbbb")
  with self.assertRaisesRegex(ContractError,"DUPLICATE_ORIGINAL_SEGMENT_START"):
   latest_original_segment(self.root,now_ns=self.now)
 def test_numeric_epoch_fixed_writer_name(self):
  self.assertTrue(ORIGINAL_SEGMENT_NAME.fullmatch("segment-1791788845839928307-deadbeef"))
  self.assertIsNone(ORIGINAL_SEGMENT_NAME.fullmatch("segment-recovery-live"))
  self.assertIsNone(ORIGINAL_SEGMENT_NAME.fullmatch("segment-999-fake"))

class TestOnlyWitnessTests(unittest.TestCase):
 def test_signature_domain_isolated_from_native_authority(self):
  self.assertEqual(DOMAIN,SIGN_DOMAIN)
  self.assertFalse(DOMAIN.startswith(b"hepta.learning-ledger.signed-evidence.v1"))
  self.assertNotIn(b"ORDER",DOMAIN)
 def test_quantile_bounded_deterministic(self):
  self.assertEqual(percentile([1,2,3,4,5],0.95),4.8)
  self.assertEqual(percentile([1,2,3,4,5],0.5),3)
 def test_create_only_signed_witness(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)
   payload={"sample":1,"broker_orders_sent":0}
   target=root/"evidence.json"
   first=private_write(target,payload)
   self.assertEqual(first,hashlib.sha256(
     json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False).encode()+b"\n").hexdigest())
   self.assertEqual(target.stat().st_mode & 0o777,0o600)
   with self.assertRaisesRegex(ContractError,"NO_REWRITING"):
    private_write(target,payload)
 def test_pre_registration_lead_and_delay_are_fixed(self):
  self.assertGreaterEqual(WINDOW_LEAD_MS,30000)
  self.assertLessEqual(DECISION_DELAY_MS,1000)


class SelfContainedTransportTests(unittest.TestCase):
 def test_orchestrator_uses_repository_included_signer_transport(self):
  from train_v2 import prewarmed_source_orchestrate as flow
  from train_v2 import prewarmed_signer_latency as signer
  import inspect
  self.assertIs(flow.SIGNER,signer.SIGNER)
  self.assertEqual(flow.DOMAIN,signer.DOMAIN)
  self.assertIs(flow.session,signer.session)
  self.assertIs(flow.exchange,signer.exchange)
  self.assertIn("StrictHostKeyChecking=yes",inspect.getsource(flow.run))
  self.assertIn("StrictHostKeyChecking=yes",inspect.getsource(signer.session))

 def test_private_plan_stays_testonly_and_no_order_send(self):
  from train_v2 import prewarmed_source_worker as worker
  import inspect
  code=inspect.getsource(worker.run)
  self.assertIn("broker_orders_sent",code)
  self.assertIn("original_llm_behavior_propensity_known",code)
  self.assertIn("native_product_ledger_ack",code)
  self.assertNotIn("placeOrder",code)
