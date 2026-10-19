"""Portable campaign-contract tests; temporary bytes are NOT historical evidence.

The actual campaign CLI retains its original private paths and frozen results.
Ordinary CI exercises the same validation with independently created fixtures;
it never reads a user's installed monitor or private completed experiment.
"""
from __future__ import annotations
import unittest
import hashlib
import json
from contextlib import ExitStack
from unittest.mock import patch
import tempfile
from pathlib import Path

from train_v2.fx_source_cache_prewarm_480 import (
    N, STEP_MS, MIN_LEAD_MS, MAX_STATUS_AGE_MS, PREWARM_DONE_LEAD_MS,
    fixed_grid, frozen_period_scope, require_prior_negative_and_pilot,
    verify_candidate, SCHEMA, original_observer,
)
from train_v2.fx_original_health_sustained import AuditHold
from train_v2 import fx_source_cache_prewarm_480 as campaign
from train_v2 import fx_verified_cache_480 as cache_contract
from train_v2 import fx_source_cache_prewarm_probe as pilot_campaign
from train_v2 import fx_original_health_sustained as source_archive
from train_v2 import fx_original_health_fullcycle as shared_archive

class Frozen480Tests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.failed=self.root/"negative-fixture.json"
        self.pilot=self.root/"pilot-fixture.json"
        self.monitor=self.root/"original-monitor.py"
        self.fast=self.root/"fast-monitor.py"
        self.cache=self.root/"cache.py"
        self.failed.write_text(json.dumps({"count":480}))
        self.pilot.write_text(json.dumps({"count":12,"operational_ready_count":11,
            "valid_original_status_count":12,"production_monitor_cutover":False}))
        self.monitor.write_text("# synthetic original monitor fixture\n")
        self.fast.write_text("# synthetic fast monitor fixture\n")
        self.cache.write_text("# synthetic cache fixture\n")
        for path in (self.failed,self.pilot,self.monitor,self.fast,self.cache):path.chmod(0o600)
        self.scope=ExitStack()
        self.addCleanup(self.scope.close)
        for name,path in (("ORIGINAL_480_FINAL",self.failed),("PILOT_12_FINAL",self.pilot)):
            self.scope.enter_context(patch.object(campaign,name,path))
            self.scope.enter_context(patch.object(campaign,name+"_SHA",self.sha(path)))
        self.scope.enter_context(patch.object(cache_contract,"ORIGINAL_MONITOR_SHA",self.sha(self.monitor)))
        self.scope.enter_context(patch.object(cache_contract,"FAST_MONITOR_SHA",self.sha(self.fast)))
        self.scope.enter_context(patch.object(cache_contract,"VERIFIED_CACHE_SHA",self.sha(self.cache)))
        self.scope.enter_context(patch.object(cache_contract,"source_file",return_value=self.fast))
        self.scope.enter_context(patch.object(cache_contract,"cache_file",return_value=self.cache))
    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    def test_fixed_480_15second_clock(self):
        self.assertEqual(N,480)
        self.assertEqual(STEP_MS,15_000)
        p=fixed_grid(1791912337000)
        self.assertEqual(len(p),480)
        self.assertTrue(all(x%15000==0 for x in p))
        self.assertTrue(all(b-a==15000 for a,b in zip(p,p[1:])))
    def test_all_480_prospectively_after_registration(self):
        t=1791912337000
        self.assertGreaterEqual(fixed_grid(t)[0]-t,MIN_LEAD_MS)
        self.assertGreater(PREWARM_DONE_LEAD_MS,10_000)
    def test_quote_age_bound_unrelaxed(self):
        self.assertEqual(MAX_STATUS_AGE_MS,30000)
    def test_distinct_research_schema(self):
        self.assertNotEqual(SCHEMA,
            "heptatrader.fx-cache-prewarm-source-health.v1")
    def test_matching_candidate_fixture_contract(self):
        self.assertTrue(verify_candidate())
    def test_distinct_negative_and_pilot_fixtures_validate(self):
        self.assertTrue(require_prior_negative_and_pilot())
    def test_mutated_original_failure_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            bad=Path(td)/"wrong.json"
            bad.write_text('{"count":480}')
            with patch("train_v2.fx_source_cache_prewarm_480.ORIGINAL_480_FINAL",bad):
                with self.assertRaisesRegex(AuditHold,
                        "PRESERVED_ORIGINAL_480_FAILURE_CHANGED"):
                    require_prior_negative_and_pilot()
    def test_mutated_previous_pilot_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            bad=Path(td)/"wrong.json"
            bad.write_text('{"count":12}')
            with patch("train_v2.fx_source_cache_prewarm_480.PILOT_12_FINAL",bad):
                with self.assertRaisesRegex(AuditHold,
                        "PRESERVED_12_CYCLE_PILOT_CHANGED"):
                    require_prior_negative_and_pilot()
    def test_missing_or_malformed_period_rejected(self):
        for x in ({}, {"original_period_sha256":"not_sha"},
                  {"original_period_sha256":"z"*64},
                  {"original_period_sha256":True}):
            with self.subTest(x=x),self.assertRaisesRegex(AuditHold,
                         "FROZEN_ORIGINAL_PAPER_PERIOD_NOT_PINNED"):
                frozen_period_scope(x)
    def test_period_field_bridging(self):
        d={"original_period_sha256":"a"*64}
        self.assertEqual(frozen_period_scope(d),{"period_sha256":"a"*64})
    def test_original_monitor_identity_uses_fixture_bytes(self):
        src=self.monitor
        self.assertEqual(original_observer(src),src)


    def test_rehashed_wrong_population_is_still_rejected(self):
        self.failed.write_text(json.dumps({"count":479}))
        with patch.object(campaign,"ORIGINAL_480_FINAL_SHA",self.sha(self.failed)):
            with self.assertRaisesRegex(AuditHold,"ORIGINAL_480_POPULATION_CHANGED"):
                require_prior_negative_and_pilot()
    def test_rehashed_wrong_pilot_semantics_are_still_rejected(self):
        original=json.loads(self.pilot.read_text())
        for key,value in (("count",11),("operational_ready_count",12),
                          ("valid_original_status_count",11),("production_monitor_cutover",True)):
            with self.subTest(key=key):
                self.pilot.write_text(json.dumps({**original,key:value}))
                with patch.object(campaign,"PILOT_12_FINAL_SHA",self.sha(self.pilot)):
                    with self.assertRaisesRegex(AuditHold,"PILOT_RESULTS_CHANGED"):
                        require_prior_negative_and_pilot()
    def test_changed_candidate_file_does_not_validate(self):
        self.cache.write_text("changed fixture cache")
        with self.assertRaisesRegex(AuditHold,"CACHE_CANDIDATE_OR_SOURCE_CONTRACT_CHANGED"):
            verify_candidate()
    def test_original_monitor_change_and_symlink_refused(self):
        self.monitor.write_text("changed original fixture")
        with self.assertRaisesRegex(AuditHold,"INSTALLED_MONITOR_IDENTITY_CHANGED"):
            original_observer(self.monitor)
        link=self.root/"linked-monitor.py"
        link.symlink_to(self.monitor)
        with patch.object(cache_contract,"ORIGINAL_MONITOR_SHA",self.sha(self.monitor)):
            with self.assertRaisesRegex(AuditHold,"INSTALLED_MONITOR_IDENTITY_CHANGED"):
                original_observer(link)
    def test_missing_fixture_cannot_claim_validation(self):
        self.failed.unlink()
        with self.assertRaises(FileNotFoundError):require_prior_negative_and_pilot()

    def make_consumer_plan(self,module):
        period=self.root/"paper-period.json"
        period.write_text(json.dumps({"execution_domain":"PAPER"}))
        period.chmod(0o600)
        config=self.root/"resident-config.json"
        config.write_text(json.dumps({
            "schema":"heptatrader.laya-resident-config.v1",
            "automatic_trading":False,"period_path":str(period),
            "period_sha256":self.sha(period),
            "feed_directory":"/home/alex/.local/state/heptatrader/synthetic-source",
        }))
        config.chmod(0o600)
        root=self.root/module.__name__.rsplit(".",1)[-1]
        if module is cache_contract:
            module.preseal(root=root,config_file=config,installed_source=self.monitor)
        else:
            module.preseal(root=root,config_path=config,installed_monitor=self.monitor)
        return root,json.loads((root/"plan.json").read_text())

    def test_all_consumer_plans_require_entry_and_shared_core_pins(self):
        for module in (cache_contract,campaign,pilot_campaign):
            with self.subTest(module=module.__name__):
                root,plan=self.make_consumer_plan(module)
                pins=plan["source_archive_code_pins"]
                self.assertEqual(pins,source_archive.code_pins())
                self.assertEqual(module.verify_plan(root)[1],plan)
                reason=("CACHE480_FROZEN_PLAN_CHANGED" if module is cache_contract
                        else "FROZEN_WARMUP_PROTOCOL_CHANGED")
                for changed in (None,{**pins,"entry_sha256":"0"*64},
                                {**pins,"core_sha256":"0"*64}):
                    altered={**plan}
                    if changed is None:del altered["source_archive_code_pins"]
                    else:altered["source_archive_code_pins"]=changed
                    (root/"plan.json").write_bytes(module.canonical(altered)+b"\n")
                    with self.assertRaisesRegex(AuditHold,reason):
                        module.verify_plan(root)

    def test_consumer_own_file_pin_cannot_hide_a_changed_helper_core(self):
        for module in (cache_contract,campaign,pilot_campaign):
            with self.subTest(module=module.__name__):
                root,plan=self.make_consumer_plan(module)
                self.assertEqual(module.verify_plan(root)[1],plan)
                read_bytes=Path.read_bytes
                def changed_core(path):
                    raw=read_bytes(path)
                    return (raw+b"\n# changed helper implementation\n"
                            if path==Path(shared_archive.__file__) else raw)
                own_key=("runner_code_sha256" if module is cache_contract
                         else "research_code_sha256")
                reason=("CACHE480_FROZEN_PLAN_CHANGED" if module is cache_contract
                        else "FROZEN_WARMUP_PROTOCOL_CHANGED")
                with patch.object(Path,"read_bytes",changed_core):
                    self.assertEqual(self.sha(Path(module.__file__)),plan[own_key])
                    self.assertEqual(source_archive.code_sha(),
                                     plan["source_archive_code_pins"]["entry_sha256"])
                    with self.assertRaisesRegex(AuditHold,reason):
                        module.verify_plan(root)

if __name__=="__main__":unittest.main()
