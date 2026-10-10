"""Adversarial Train V2 canonical-entry preflight. No host trust is minted."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from train_v2.adapters import make_spec
from train_v2.causal_ttl import sealed_original_broker_expiry
from train_v2.contracts import ContractError, digest
from train_v2.canonical_native import (
    FIELDS, WRITER_METHODS, SOURCE, PIN, rust_fields, verify_native_layout,
)
from train_v2.canonical_admission import inspect_readiness, BLOCKERS
from test_train_v2_phase2 import OriginalSourceFixture

class ReadinessTests(OriginalSourceFixture):
 def setUp(self):
  super().setUp()
  self.spec=make_spec(self.old_spec["period_sha256"],"PAPER_FIXTURE_BEHAVIOR",
             broker_reported_expiry=True) if hasattr(self,"old_spec") else (
    make_spec(self.spec["period_sha256"],"PAPER_FIXTURE_BEHAVIOR",
              broker_reported_expiry=True))
  self.recs,_=sealed_original_broker_expiry(self.path,self.spec)
  self.decision=self.recs[-1]["available_at_ms"]+200
  self.origin=self.recs[-1]
  self.layout=self.fixture_layout()

 @staticmethod
 def fixture_layout():
  # TEST-ONLY invented source hashes exercise the fail-closed inspector,
  # never report actual Rust source attestation from this fixture.
  r={
   "schema":"heptatrader.train-v2-pinned-native-layout.v1",
   "exact_native_commit":PIN,
   "original_rust_fields":{k:list(v) for k,v in FIELDS.items()},
   "writer_methods":list(WRITER_METHODS),
   "native_source_sha256":{key:"a"*64 for key in SOURCE},
   "full_canonical_workspace_dependencies_present":False,
   "native_ledger_writer_compiled":False,
   "native_ledger_writer_executed":False,
   "canonical_host_trust_enrolled":False,
   "trade_authority":False,"broker_orders_sent":0}
  return {**r,"layout_id":digest(r)}

 def check(self,*,row=None,layout=None,decision=None,confidence=None):
  return inspect_readiness(self.layout if layout is None else layout,
    self.spec,self.origin if row is None else row,self.path,
    self.decision if decision is None else decision,confidence)

 def test_true_quote_still_holds_twelve_native_product_requirements(self):
  r=self.check()
  self.assertEqual(r["assessment"],
   "HOLD_MISSING_NATIVE_LEDGER_WRITER_AND_OWNER_FACTS")
  self.assertEqual(r["missing_native_requirements"],list(BLOCKERS))
  self.assertEqual(len(BLOCKERS),12)
  self.assertFalse(r["native_decision_appended"])
  self.assertFalse(r["canonical_owner_current_selected"])
  self.assertFalse(r["trade_authority"])
  self.assertEqual(r["label_preoutcome_status"],"PENDING_OUTCOME")
  self.assertEqual(r["broker_orders_sent"],0)

 def test_remote_confidence_never_becomes_logged_propensity(self):
  r=self.check(confidence=".99")
  self.assertTrue(r["remote_confidence_present"])
  self.assertFalse(r["remote_confidence_is_logged_behavior_propensity"])
  self.assertIn("TRUE_LOGGED_BEHAVIOR_PROPENSITY_NOT_ATTESTED",
                r["missing_native_requirements"])

 def test_invalid_or_out_of_range_confidence_rejected(self):
  for value in ("NaN","inf","-0.1","1.01",True):
   with self.subTest(value=value),self.assertRaises(ContractError):
    self.check(confidence=value)

 def test_original_identity_and_price_mutation_refused(self):
  for field,replacement in (("source_id","f"*64),("bid","1.234"),
                            ("source_sha256","d"*64),
                            ("valid_until_ms",self.origin["valid_until_ms"]+1000)):
   row=dict(self.origin)
   row[field]=replacement
   with self.subTest(field=field),self.assertRaisesRegex(
       ContractError,"CANONICAL_SOURCE_NOT_EXACT_ORIGINAL"):
    self.check(row=row)

 def test_forged_native_success_and_propensity_fields_refused(self):
  for field in ("native_writer_ack","native_selected_propensity",
                "canonical_agentd_owner_accepted","native_trust_enrolled"):
   row=dict(self.origin)
   row[field]=True
   with self.subTest(field=field),self.assertRaisesRegex(
       ContractError,"RESEARCH_EVENT_CANNOT_SELF_MINT_NATIVE"):
    self.check(row=row)

 def test_wrong_versioned_layout_hash_refused(self):
  layout=dict(self.layout)
  layout["native_source_sha256"]={"different":"0"*64}
  with self.assertRaisesRegex(ContractError,"CANONICAL_SOURCE_LAYOUT_NOT_VERIFIED"):
   self.check(layout=layout)

 def test_rehashed_fake_native_compiled_claim_refused(self):
  layout=dict(self.layout);layout.pop("layout_id")
  layout["native_ledger_writer_compiled"]=True
  layout["layout_id"]=digest(layout)
  with self.assertRaisesRegex(ContractError,
                            "CANONICAL_NATIVE_LAYOUT_NOT_SAFE_OR_COMPLETE"):
   self.check(layout=layout)

 def test_future_or_expired_quote_not_reclassified(self):
  with self.assertRaises(ContractError):
   self.check(decision=self.origin["valid_until_ms"]+1)

 def test_source_witness_old_quote_not_auto_selected(self):
  with self.assertRaises(ContractError):
   self.check(row=self.recs[0])

 def test_trade_authority_claim_denied(self):
  row=dict(self.origin);row["trade_authority"]=True
  with self.assertRaisesRegex(ContractError,"RESEARCH_EVENT_CANNOT_GRANT"):
   self.check(row=row)

class NativeShapeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory()
  self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name)
  sample={
   "decision":self.struct("ProductionDecisionV2",FIELDS["ProductionDecisionV2"])
       +"\n".join("pub fn "+method+"(&self) {}" for method in WRITER_METHODS),
   "causal":self.struct("CandidateSetCompletenessReceiptV1",
                      FIELDS["CandidateSetCompletenessReceiptV1"]),
   "signed":self.struct("SignedLearningEvidenceV1",
                      FIELDS["SignedLearningEvidenceV1"]),
   "witness":"pub struct LedgerWitnessStore {}",
   "dataset":"pub struct DatasetSnapshotReceiptV3 {}",
   "owner":"pub struct ArtifactOwnerVerifierV1 {}",
   "model":"// model source fixture",
   "trust":"// trust source fixture",
  }
  for name,relative in SOURCE.items():
   dst=self.root/relative
   dst.parent.mkdir(parents=True,exist_ok=True)
   dst.write_text(sample[name])

 @staticmethod
 def struct(name,fields):
  return ("pub struct "+name+" {\n"
          + "".join("    pub "+x+": u64,\n" for x in fields) + "}\n")

 def verify(self):
  with patch("train_v2.canonical_native.pinned_commit") as pin:
   with patch("train_v2.canonical_native.subprocess.run",
              return_value=SimpleNamespace(returncode=0)):
    result=verify_native_layout(self.root)
   pin.assert_called_once()
  return result

 def test_real_field_name_parser_and_no_executable_claim(self):
  receipt=self.verify()
  self.assertEqual(receipt["exact_native_commit"],PIN)
  self.assertEqual(receipt["original_rust_fields"],
                   {name:list(fields) for name,fields in FIELDS.items()})
  self.assertFalse(receipt["native_ledger_writer_executed"])
  self.assertFalse(receipt["full_canonical_workspace_dependencies_present"])

 def test_missing_native_prod_field_rejected(self):
  p=self.root/SOURCE["decision"]
  p.write_text(p.read_text().replace("pub selected_propensity: u64,\n",""))
  with self.assertRaisesRegex(ContractError,"CANONICAL_NATIVE_FIELDS_CHANGED"):
   self.verify()

 def test_missing_witness_api_rejected(self):
  (self.root/SOURCE["witness"]).write_text("// no witness")
  with self.assertRaisesRegex(ContractError,"CANONICAL_PRODUCT_API_NOT_PRESENT"):
   self.verify()

 def test_changed_native_method_rejected(self):
  p=self.root/SOURCE["decision"]
  p.write_text(p.read_text().replace("pub fn append_decision","pub fn append_fake"))
  with self.assertRaisesRegex(ContractError,"CANONICAL_NATIVE_LEDGER_METHOD_CHANGED"):
   self.verify()

 def test_tracked_source_dirty_rejected(self):
  with patch("train_v2.canonical_native.pinned_commit"):
   with patch("train_v2.canonical_native.subprocess.run",
              return_value=SimpleNamespace(returncode=1)):
    with self.assertRaisesRegex(ContractError,"CANONICAL_NATIVE_TRACKED_SOURCE_DIRTY"):
     verify_native_layout(self.root)

 def test_wrong_head_commit_rejected(self):
  with patch("train_v2.canonical_native.subprocess.run",
             return_value=SimpleNamespace(stdout="badcommit\n",returncode=0)):
   with self.assertRaisesRegex(ContractError,"CANONICAL_SOURCE_COMMIT_DRIFT"):
    verify_native_layout(self.root)

 def test_symlink_native_source_refused(self):
  p=self.root/SOURCE["model"]
  dest=p.with_suffix(".other")
  p.rename(dest);p.symlink_to(dest)
  with patch("train_v2.canonical_native.pinned_commit"):
   with self.assertRaisesRegex(ContractError,"CANONICAL_NATIVE_SOURCE_MISSING"):
    verify_native_layout(self.root)
