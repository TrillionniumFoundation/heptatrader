"""Synthetic-only cohort rollover: no service mutation, provider call or order."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import json
import os
import time
import unittest
from hepta_decision_attribution import canonical,digest
from laya_cell import value343_prospective as s
from test_value343_realcode_isolated_e2e import OriginalSyntheticFixture

def private_json(path,value):
    path.write_text(canonical(value))
    path.chmod(0o600)
    return hashlib.sha256(path.read_bytes()).hexdigest()

@contextmanager
def trial():
    with TemporaryDirectory() as td,ExitStack() as stack:
        root=Path(td)
        for key in ("ROLLOVER_CONFIG","ROLLOVER_CONFIG_PATH","ROLLOVER_CONFIG_SHA",
                    "COHORT","OUT","SCHEMA","SCORE_SCHEMA","STUDY","STUDY_SHA"):
            stack.enter_context(patch.object(s,key,getattr(s,key)))
        now=int(time.time()*1000)
        report={"producer_manifest_sha256":s.TRAINING_COHORT,
            "program_sha256":s.SOURCE_CODE_SHA,"snapshot_rows_total":343,
            "fixed_behavior_rows":335,"train_rows":217,"historical_holdout_rows":67,
            "model_qualification":"HISTORICAL_TIME_SPLIT_RESEARCH_ONLY",
            "model_promoted":False,"economic_promotion_supported":False,
            "model":{"actual_optimizer_steps":20,"features":list(s.prior.FEATURES),
                     "coefficients":[-.1]+[0.]*len(s.prior.FEATURES)},
            "normalizer":{"means":[0.]*len(s.prior.FEATURES),
                          "scales":[1.]*len(s.prior.FEATURES),"clip_after_train_scaling":5},
            "train_baseline_constants_usd":{"training_median_usd":-12.},
            "train_request_end_ms":now-30000,"train_final_outcome_known_ms":now-20000,
            "report_id":"SYNTHETIC_MODEL_NOT_A_MARKET_RESULT"}
        s.STUDY=root/"synthetic-model.json"
        s.STUDY_SHA=private_json(s.STUDY,report)
        old=root/"previous"
        old.mkdir(mode=0o700)
        previous=old/"freeze.json"
        oldsha=private_json(previous,{"schema":"heptatrader.value343-predecision-two-sided-shadow.v5",
            "model_sha256":s.STUDY_SHA,"behavior_manifest_sha256":s.TRAINING_COHORT,
            "activated_at_ms":now-10000,"broker_orders_sent":0})
        manifest=root/"manifest.json"
        period="e"*64
        cohort=private_json(manifest,{"schema":"heptatrader.attribution-producer.v1",
            "instrument":"EUR.USD","execution_domain":"PAPER","period_sha256":period,
            "journal_directory":str(root/"original")})
        cfg={"schema":"heptatrader.value343-rollover-config.v1",
             "output_directory":str(root/"new-trial"),
             "source_manifest_path":str(manifest),"source_manifest_sha256":cohort,
             "source_period_sha256":period,"previous_freeze_path":str(previous),
             "previous_freeze_sha256":oldsha}
        path=root/"registration.json"
        private_json(path,cfg)
        s.configure(path)
        producer={"unit":"heptatrader-paper-driver.service","pid":123,
                  "process_start_ticks":456,"manifest_path":str(manifest),
                  "manifest_sha256":cohort,"observed_at_ms":now}
        yield root,cfg,report,producer

class ProducerRolloverTests(unittest.TestCase):
    def test_historical_training_cohort_does_not_follow_prospective_cohort(self):
        with trial() as (root,cfg,report,producer):
            self.assertNotEqual(s.COHORT,s.TRAINING_COHORT)
            self.assertEqual(s.read_model()[0]["producer_manifest_sha256"],s.TRAINING_COHORT)
            report["producer_manifest_sha256"]=s.COHORT
            s.STUDY_SHA=private_json(s.STUDY,report)
            with self.assertRaisesRegex(ValueError,"FROZEN_HISTORICAL_MODEL_SCOPE_CHANGED"):
                s.read_model()

    def test_registers_new_forward_trial_without_mutating_or_waiting_on_previous(self):
        with trial() as (root,cfg,report,producer):
            old=Path(cfg["previous_freeze_path"])
            original=old.read_bytes()
            with patch.object(s,"running_producer",return_value=producer):
                self.assertEqual(s.initialize(),0)
            frozen=s.frozen()
            self.assertEqual(frozen["behavior_manifest_sha256"],cfg["source_manifest_sha256"])
            self.assertEqual(frozen["historical_training_behavior_manifest_sha256"],s.TRAINING_COHORT)
            self.assertEqual(frozen["producer_at_registration"]["pid"],123)
            self.assertLess(frozen["train_final_outcome_known_ms"],frozen["activated_at_ms"])
            self.assertFalse(frozen["historical_model_retrained"])
            self.assertEqual(old.read_bytes(),original)
            with self.assertRaises(FileExistsError):s.initialize()

    def test_foreign_live_producer_refuses_registration_before_creating_trial(self):
        with trial() as (root,cfg,report,producer):
            with patch.object(s,"running_producer",side_effect=ValueError("ROLLOVER_PAPER_PRODUCER_MANIFEST_MISMATCH")):
                with self.assertRaisesRegex(ValueError,"PRODUCER_MANIFEST_MISMATCH"):
                    s.initialize()
            self.assertFalse(s.OUT.exists())

    def test_training_outcome_must_be_known_before_registration(self):
        with trial() as (root,cfg,report,producer):
            report["train_final_outcome_known_ms"]=int(time.time()*1000)+100000
            with patch.object(s,"read_model",return_value=(report,)),patch.object(s,"running_producer",return_value=producer):
                with self.assertRaisesRegex(ValueError,"TRAINING_NOT_COMPLETE_BEFORE_REGISTRATION"):
                    s.initialize()
            self.assertFalse(s.OUT.exists())

    def test_config_and_predecessor_are_immutable_inputs(self):
        for target in ("config","previous"):
            with self.subTest(target=target),trial() as (root,cfg,report,producer):
                with patch.object(s,"running_producer",return_value=producer):s.initialize()
                path=s.ROLLOVER_CONFIG_PATH if target=="config" else Path(cfg["previous_freeze_path"])
                path.write_bytes(path.read_bytes()+b" ")
                with self.assertRaisesRegex(ValueError,"ROLLOVER_(CONFIG|PREVIOUS_FREEZE)_CHANGED"):
                    s.frozen()

    def test_new_output_cannot_overlap_predecessor(self):
        with trial() as (root,cfg,report,producer):
            cfg["output_directory"]=str(Path(cfg["previous_freeze_path"]).parent/"child")
            path=root/"overlap.json";private_json(path,cfg)
            with self.assertRaisesRegex(ValueError,"PRESERVE_PREVIOUS_STUDY"):s.configure(path)

    def test_actual_unit_pid_must_use_the_registered_manifest(self):
        with trial() as (root,cfg,report,producer):
            pid=123
            proc=root/"proc"/str(pid);proc.mkdir(parents=True)
            (proc/"stat").write_text(str(pid)+" (synthetic driver) "+" ".join(["S"]+["0"]*18+["456"]+["0"]*4))
            script=str(Path(cfg["source_manifest_path"]).parent/"heptatrader_attributed_driver.py")
            argv=["python3",script,"--manifest",cfg["source_manifest_path"]]
            (proc/"cmdline").write_bytes(chr(0).join(argv).encode()+bytes([0]))
            def paths(value):return root/"proc" if value=="/proc" else Path(value)
            with patch.object(s,"Path",side_effect=paths),patch.object(s.subprocess,"run",return_value=SimpleNamespace(stdout=str(pid))):
                observed=s.running_producer()
                self.assertEqual(observed["process_start_ticks"],456)
                argv[-1]="/synthetic/foreign/manifest.json"
                (proc/"cmdline").write_bytes(chr(0).join(argv).encode()+bytes([0]))
                with self.assertRaisesRegex(ValueError,"PRODUCER_MANIFEST_MISMATCH"):
                    s.running_producer()
                self.assertEqual(s.producer_status()["status"],"BOUND_PRODUCER_NOT_CURRENT")

    def test_only_new_cohort_forward_request_reaches_fsync_response_and_fee_score(self):
        with trial() as (root,cfg,report,producer):
            with patch.object(s,"running_producer",return_value=producer):s.initialize()
            fixed=s.frozen()
            world=OriginalSyntheticFixture(root)
            old=json.loads(world.file.read_bytes())
            old["producer_id"]="c"*32;old["data"]["manifest_sha256"]=s.TRAINING_COHORT
            old.pop("sha256");old["sha256"]=digest(old)
            foreign=world.dir/("c"*32+"-0000.jsonl")
            foreign.write_text(canonical(old)+chr(10));foreign.chmod(0o600)
            events,requests=s.original_events(s.registration())
            self.assertEqual(len(events),1)
            old_request=world.request("b"*32)
            old_request["started_at_ms"]=fixed["activated_at_ms"]-1
            world.append("REQUEST",old_request)
            events,requests=s.original_events(s.registration())
            result=s.capture(events,requests,fixed,s.read_model())
            self.assertEqual(result["old_skipped"],1)
            self.assertEqual(result["new_valid_predictions"],0)
            time.sleep(.005)
            req=world.request("a"*32)
            req["started_at_ms"]=fixed["activated_at_ms"]+1
            world.append("REQUEST",req)
            events,requests=s.original_events(s.registration())
            self.assertEqual(s.capture(events,requests,fixed,s.read_model())["new_valid_predictions"],1)
            self.assertTrue((s.OUT/"durable_seals"/(req["request_id"]+".json")).exists())
            time.sleep(.005);world.response(req)
            events,requests=s.original_events(s.registration())
            self.assertEqual(s.attest_responses(events,requests,fixed)["verified_new"],1)
            time.sleep(.005)
            decision,binding=world.decision_and_binding(req)
            oms,rewards,net=world.economics(req,binding)
            events,requests=s.original_events(s.registration())
            with patch.object(s.prior,"REWARDS",rewards),patch.object(s.prior,"OMS",oms):
                self.assertEqual(s.score(events,requests,fixed)["new_scores"],1)
            score=json.loads((s.OUT/"scores"/(req["request_id"]+".json")).read_bytes())
            self.assertTrue(score["score_valid"])
            self.assertEqual(score["broker_orders_sent"],0)
            self.assertFalse(score["model_promotion"])
            self.assertEqual(s.TRAINING_COHORT,report["producer_manifest_sha256"])

if __name__=="__main__":unittest.main()
