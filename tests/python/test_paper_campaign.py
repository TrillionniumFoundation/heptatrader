"""Synthetic host driver contract tests; no Broker connection or credentials."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from hepta_evidence_io import EvidenceError, load_json, write_json, sha256_file
from hepta_paper_campaign import CampaignStore
from hepta_ib_paper_harness import run, PinnedDriver
from paper_rollout_fixtures import RolloutFixture


class FixtureDriver:
    """Only an in-memory test double. Production CLI cannot select this class."""
    def __init__(self, fixture, fail=None):
        self.f=fixture; self.digest=fixture.binding['driver_sha256']; self.calls=[]
        self.leg=0; self.barriers=0; self.command=None; self.fail=fail
    def call(self,operation,payload):
        self.calls.append(operation)
        if operation==self.fail: raise TimeoutError('injected driver timeout')
        if operation=='inspect':
            return dict(binding=self.f.binding,account_mode='PAPER',profile_order_mode='EXTERNAL_P1_CANARY_LMT_DAY',
                        network_isolated=True,credential_isolated=True)
        if operation=='barrier':
            i=self.barriers//2; key='before' if self.barriers%2==0 else 'after'; self.barriers+=1
            return copy.deepcopy(self.f.snapshots['cycles'][i][key])
        if operation in ('place','flatten'):
            self.command=payload; return dict(command_id=payload['command_id'],status='accepted')
        if operation=='await_terminal':
            rows=copy.deepcopy(self.f.journal[self.leg*3:self.leg*3+3])
            callbacks=copy.deepcopy(self.f.callbacks[self.leg*2:self.leg*2+2]); self.leg+=1
            for item in rows+callbacks:
                item['cycle_id']=payload['cycle_id'];item['command_id']=payload['command_id']
            return dict(command_id=payload['command_id'],status='terminal',journal=rows,callbacks=callbacks)
        raise AssertionError(operation)


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.f=RolloutFixture(self.root/'fixture')
        self.store=CampaignStore(self.root/'store',self.f.campaign,self.f.binary,self.f.harness)
    def tearDown(self): self.tmp.cleanup()
    def operation(self,stage):
        def operation(evidence):
            f=RolloutFixture(self.root/('source-'+stage),stage)
            shutil.copytree(f.evidence,evidence)
        return operation
    def test_resume_does_not_repeat_successful_mutations(self):
        first=self.store.run('canary',self.operation('canary'))
        resumed=CampaignStore(self.root/'store',self.f.campaign,self.f.binary,self.f.harness)
        self.assertEqual(first,resumed.run('canary',lambda _:self.fail('must not resend')))
        resumed.run('pilot',self.operation('pilot'));resumed.run('extended',self.operation('extended'))
        self.assertEqual(set(resumed.status()['completed']),{'canary','pilot','extended'})
    def test_promotion_requires_previous_actual_evidence(self):
        with self.assertRaises(EvidenceError): self.store.run('pilot',lambda _:self.fail())
    def test_failure_retains_attempt_and_blocks_retry(self):
        def fail(evidence):
            evidence.mkdir();(evidence/'diagnostic').write_text('possibly sent');raise TimeoutError('injected')
        with self.assertRaises(TimeoutError):self.store.run('canary',fail)
        self.assertIsNotNone(self.store.state()['active'])
        self.assertEqual(len(list((self.root/'store'/'attempts').glob('*/diagnostic'))),1)
        with self.assertRaises(EvidenceError): self.store.run('canary',lambda _:self.fail('must not retry'))
    def test_completion_write_failure_retains_fence_and_original_error(self):
        real_write=write_json
        def fail_completion(path,value,**kwargs):
            if path==self.store.state_path and value.get('active') is None:
                raise OSError('injected completion fsync failure')
            return real_write(path,value,**kwargs)
        with patch('hepta_paper_campaign.write_json',side_effect=fail_completion):
            with self.assertRaisesRegex(OSError,'completion fsync'):
                self.store.run('canary',self.operation('canary'))
        self.assertNotIn('canary',self.store.state()['completed'])
        self.assertEqual(self.store.state()['active']['status'],'failed_or_interrupted')
        self.assertEqual(len(list((self.root/'store'/'attempts').glob('*/rollout-verification.json'))),1)
        with self.assertRaises(EvidenceError):
            self.store.run('canary',lambda _:self.fail('must not resend'))

    def test_saved_evidence_and_receipts_are_reverified(self):
        self.store.run('canary',self.operation('canary'))
        record=self.store.state()['completed']['canary']
        evidence=self.root/'store'/'attempts'/record['attempt']
        (evidence/'snapshot.json').write_text('{}')
        with self.assertRaises(EvidenceError):self.store.run('pilot',lambda _:self.fail())
    def test_identity_and_code_drift_reject(self):
        value=load_json(self.f.campaign); value['binding']['profile_sha256']='0'*64
        write_json(self.f.campaign,value,replace=True)
        with self.assertRaises(EvidenceError):CampaignStore(self.root/'store',self.f.campaign,self.f.binary,self.f.harness)
        value['binding']['controller_sha256']='0'*64;write_json(self.f.campaign,value,replace=True)
        with self.assertRaises(EvidenceError):CampaignStore(self.root/'other',self.f.campaign,self.f.binary,self.f.harness)
    def test_binary_drift_blocks_before_mutation(self):
        self.f.binary.chmod(0o600);self.f.binary.write_bytes(b'changed')
        with self.assertRaises(EvidenceError):self.store.run('canary',lambda _:self.fail())
    def test_lock_prevents_concurrent_campaign(self):
        with self.store.locked():
            with self.assertRaises(EvidenceError):self.store.run('canary',lambda _:self.fail())
    def test_running_state_after_crash_blocks_reentry(self):
        write_json(self.store.state_path,dict(schema='heptatrader.paper-campaign-state.v1',completed={},
                   active=dict(stage='canary',attempt='canary-old',status='running')))
        with self.assertRaises(EvidenceError):self.store.run('canary',lambda _:self.fail())
    def test_stage_cannot_reuse_evidence_from_earlier_time(self):
        self.store.run('canary',self.operation('canary'))
        def operation(evidence):
            f=RolloutFixture(self.root/'stale-pilot','pilot')
            for cycle in f.snapshots['cycles']:
                for key in ('before','after'):cycle[key]['observed_at_ms']-=10000
            for row in f.journal+f.callbacks:row['observed_at_ms']-=10000
            f.save();shutil.copytree(f.evidence,evidence)
        with self.assertRaises(EvidenceError):self.store.run('pilot',operation)


class HarnessTests(unittest.TestCase):
    def test_all_stages_cross_check_each_cycle(self):
        for stage,count in [('canary',1),('pilot',3),('extended',10)]:
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as d:
                root=Path(d);f=RolloutFixture(root/'fixture',stage);driver=FixtureDriver(f)
                result=run(driver,f.campaign,f.binary,f.harness,stage,root/'output')
                self.assertEqual(result['mutation_cycles'],count)
                self.assertEqual(driver.calls.count('place'),count)
                self.assertEqual(driver.calls.count('flatten'),count)
                self.assertFalse(result['paper_authorized'])
    def test_uncertain_place_never_retries_or_guesses_flatten(self):
        for fail in ['place','await_terminal']:
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as d:
                root=Path(d);f=RolloutFixture(root/'fixture');driver=FixtureDriver(f,fail)
                with self.assertRaises(TimeoutError):run(driver,f.campaign,f.binary,f.harness,'canary',root/'output')
                self.assertEqual(driver.calls.count('place'),1)
                self.assertNotIn('flatten',driver.calls)
                self.assertEqual(len(list((root/'output').glob('*.intent.json'))),1)
    def test_empty_economic_proof_blocks_next_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);f=RolloutFixture(root/'fixture','pilot');f.callbacks[0]['execution_id']=''
            driver=FixtureDriver(f)
            with self.assertRaises(EvidenceError):run(driver,f.campaign,f.binary,f.harness,'pilot',root/'output')
            self.assertEqual(driver.calls.count('place'),1)
    def test_nonflat_start_never_places(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);f=RolloutFixture(root/'fixture');f.snapshots['cycles'][0]['before']['complete']=False
            driver=FixtureDriver(f)
            with self.assertRaises(EvidenceError):run(driver,f.campaign,f.binary,f.harness,'canary',root/'output')
            self.assertNotIn('place',driver.calls)
    def test_changed_driver_is_rejected_before_inspection(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);f=RolloutFixture(root/'fixture');driver=FixtureDriver(f);driver.digest='0'*64
            with self.assertRaises(EvidenceError):run(driver,f.campaign,f.binary,f.harness,'canary',root/'output')
            self.assertEqual(driver.calls,[])
    def test_pinned_driver_failure_keeps_nonsecret_request(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);driver_file=root/'driver';driver_file.write_text('#!/bin/sh\nexit 42\n');driver_file.chmod(0o500)
            # Production checks root ownership. Simulate that metadata without
            # needing privileges on the ordinary CI runner.
            from types import SimpleNamespace
            info=driver_file.stat()
            with patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=info.st_mode,st_nlink=1,st_uid=0)):
                driver=PinnedDriver(driver_file,sha256_file(driver_file),root)
            with self.assertRaises(EvidenceError):driver.call('inspect',{})
            self.assertEqual(len(list((root/'driver-calls').glob('*/request.json'))),1)

if __name__=='__main__':unittest.main()
