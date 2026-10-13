"""Only future sealed plans may opt in to the faster original quote reader."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from laya_cell.integrity import ContractError,digest,sha_file
from laya_cell.resident_source_policy import (
    RECENT,RUNTIME_PIN,declared_profile,read_for_future_plan)
from laya_cell.resident import Supervisor,config_check
from laya_cell.promotion import RULE


class FutureReaderPlanTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)
  self.runtime=self.root/"frozen-runtime"
  for i,rel in enumerate(RUNTIME_PIN):
   target=self.runtime/rel
   target.parent.mkdir(parents=True,exist_ok=True)
   target.write_text(f"reviewed runtime module {i}\n")
  self.config={
    "period_sha256":"a"*64,
    "runtime_directory":str(self.runtime),
    "runtime_sha256":{name:sha_file(self.runtime/name)
                      for name in RUNTIME_PIN},
    "future_source_read_profile":RECENT}
  self.plan={
    "schema":"heptatrader.laya-resident-future-plan.v1",
    "future_source_read_profile":RECENT,
    "evaluation_runtime_sha256":dict(self.config["runtime_sha256"]),
    "rule":RULE,
    "scope":self.config["period_sha256"],
  }
  self.plan["plan_id"]=digest(self.plan)

 def test_opted_and_sealed_new_plan_selects_new_reader(self):
  with patch("laya_cell.resident_recent_source.read_recent_original_quotes",
             return_value=(["one"],["pin"])) as fast:
   quotes,pins,mode=read_for_future_plan(self.config,self.plan,123)
  self.assertEqual((quotes,pins,mode),(["one"],["pin"],RECENT))
  fast.assert_called_once()

 def test_legacy_plan_still_uses_unmodified_full_reader(self):
  older=dict(self.plan)
  older.pop("future_source_read_profile")
  older["plan_id"]=digest({k:v for k,v in older.items() if k!="plan_id"})
  with patch("laya_cell.resident_data.read_quotes",
             return_value=(["full"],["original-pin"])) as old:
   q,p,mode=read_for_future_plan(self.config,older,123)
  self.assertEqual((q,p,mode),(["full"],["original-pin"],"ORIGINAL_FULL_HISTORY_V1"))
  old.assert_called_once()

 def test_predict_preflights_missing_source_pin_before_weight_import(self):
  # CI's no-GPU Python lane deliberately has no torch dependency. Validate
  # source function ordering without importing a GPU model or weakening CI.
  import ast
  source=Path(__file__).resolve().parents[1]/'resident_predict.py'
  text=source.read_text()
  tree=ast.parse(text)
  fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='predict')
  body=ast.get_source_segment(text,fn)
  self.assertLess(body.index('expected_source_profile=declared_profile'),
                  body.index('from safetensors.torch import load_file'))
  self.assertLess(body.index('expected_source_profile=declared_profile'),
                  body.index('load_verified_laya('))
  forged=dict(self.plan)
  forged['evaluation_runtime_sha256']=dict(self.plan['evaluation_runtime_sha256'])
  forged['evaluation_runtime_sha256'][RUNTIME_PIN[0]]='f'*64
  forged['plan_id']=digest({k:v for k,v in forged.items() if k!='plan_id'})
  with self.assertRaisesRegex(ContractError,'FUTURE_RECENT_SOURCE_CODE_NOT_PINNED'):
   declared_profile(self.config,forged)

 def test_unknown_profile_refused_without_fallback(self):
  forged=dict(self.plan);forged["future_source_read_profile"]="FAST_UNTRUSTED"
  forged["plan_id"]=digest({k:v for k,v in forged.items() if k!="plan_id"})
  with self.assertRaisesRegex(ContractError,"PROFILE_NOT_ENROLLED"):
   declared_profile(self.config,forged)

 def test_cannot_upgrade_past_frozen_plan_by_adding_new_field(self):
  forged=dict(self.plan);forged["evaluation_runtime_sha256"]=dict(self.plan["evaluation_runtime_sha256"])
  forged["evaluation_runtime_sha256"][RUNTIME_PIN[0]]="d"*64
  with self.assertRaisesRegex(ContractError,"PLAN_NOT_SEALED"):
   declared_profile(self.config,forged)

 def test_rehashed_new_plan_does_not_escape_runtime_pins(self):
  forged=dict(self.plan)
  forged["evaluation_runtime_sha256"]=dict(self.plan["evaluation_runtime_sha256"])
  forged["evaluation_runtime_sha256"][RUNTIME_PIN[1]]="d"*64
  forged["plan_id"]=digest({k:v for k,v in forged.items() if k!="plan_id"})
  with self.assertRaisesRegex(ContractError,"CODE_NOT_PINNED"):
   declared_profile(self.config,forged)

 def test_stale_actual_runtime_bytes_refused_despite_matching_plan_pins(self):
  path=self.runtime/RUNTIME_PIN[0]
  path.write_text('new unreviewed bytes')
  with self.assertRaisesRegex(ContractError,'FUTURE_RECENT_SOURCE_RUNTIME_HASH_CHANGED'):
   declared_profile(self.config,self.plan)

 def test_runtime_root_without_pinned_existing_modules_refused(self):
  conf=dict(self.config)
  conf['runtime_directory']=str(self.root/'missing-runtime')
  with self.assertRaisesRegex(ContractError,'FUTURE_RECENT_SOURCE_RUNTIME_DIRECTORY_UNPINNED'):
   declared_profile(conf,self.plan)

 def test_config_and_plan_must_independently_opt_in(self):
  conf=dict(self.config)
  conf.pop("future_source_read_profile")
  with self.assertRaisesRegex(ContractError,"PROFILE_NOT_ENROLLED"):
   declared_profile(conf,self.plan)

 def test_missing_candidate_source_module_pin_refused(self):
  conf=dict(self.config);conf["runtime_sha256"]=dict(self.config["runtime_sha256"])
  del conf["runtime_sha256"][RUNTIME_PIN[0]]
  with self.assertRaisesRegex(ContractError,"CODE_NOT_PINNED"):
   declared_profile(conf,self.plan)

 def test_new_plan_created_with_two_new_explicit_source_pins(self):
  temp=self.root
  control=temp/"control";(control/"owner-outbox").mkdir(parents=True)
  evaluation=temp/"evaluation";evaluation.mkdir()
  cand=temp/"candidate";cand.mkdir()
  (cand/"candidate.json").write_text('{"placeholder":true}')
  job={"output_directory":str(cand),"parent_directory":str(temp/"parent"),
       "parent_id":"parent-id"}
  candidate={
   "candidate_id":"candidate-id","dataset_id":"dataset-id",
   "parent_id":"parent-id","plasticity":{"proposal_digest":"b"*64}}
  object_=Supervisor.__new__(Supervisor)
  object_.c={"evaluation_directory":str(evaluation),"control_directory":str(control),
             "runtime_sha256":dict(self.config["runtime_sha256"]),
             "period_sha256":"a"*64,"future_source_read_profile":RECENT}
  object_.root=control;object_.db=sqlite3.connect(":memory:")
  self.addCleanup(object_.db.close)
  with patch("laya_cell.resident.get_meta",return_value=0),patch(
      "laya_cell.resident.put_meta",return_value=None),patch(
       "laya_cell.resident.rule_for_trial",return_value=RULE):
   object_.register_evaluation("job-one",candidate,job)
  plan=json.loads((evaluation/"job-one"/"plan.json").read_text())
  self.assertEqual(plan["future_source_read_profile"],RECENT)
  self.assertEqual(plan["plan_id"],digest({k:v for k,v in plan.items() if k!="plan_id"}))
  for rel in RUNTIME_PIN:
   self.assertEqual(plan["evaluation_runtime_sha256"][rel],
                    self.config["runtime_sha256"][rel])
  self.assertFalse(plan["broker_orders_sent"])

 def test_legacy_plan_registration_does_not_gain_fast_reader(self):
  temp=self.root
  control=temp/"legacy-control";(control/"owner-outbox").mkdir(parents=True)
  eval_dir=temp/"legacy-evaluation";eval_dir.mkdir()
  cand=temp/"legacy-candidate";cand.mkdir()
  (cand/"candidate.json").write_text('{"placeholder":true}')
  object_=Supervisor.__new__(Supervisor)
  object_.c={"evaluation_directory":str(eval_dir),
             "runtime_sha256":{},"period_sha256":"a"*64}
  object_.root=control;object_.db=sqlite3.connect(":memory:")
  self.addCleanup(object_.db.close)
  candidate={"candidate_id":"legacy-id","dataset_id":"ds","parent_id":"prior",
             "plasticity":{"proposal_digest":"b"*64}}
  job={"output_directory":str(cand),"parent_directory":str(temp/"prior"),
       "parent_id":"prior"}
  with patch("laya_cell.resident.get_meta",return_value=0),patch(
      "laya_cell.resident.put_meta",return_value=None),patch(
       "laya_cell.resident.rule_for_trial",return_value=RULE):
   object_.register_evaluation("legacy-job",candidate,job)
  plan=json.loads((eval_dir/"legacy-job"/"plan.json").read_text())
  self.assertNotIn("future_source_read_profile",plan)
  self.assertNotIn(RUNTIME_PIN[0],plan["evaluation_runtime_sha256"])
