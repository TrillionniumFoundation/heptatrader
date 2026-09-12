from __future__ import annotations
import copy
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from hepta_evidence_io import EvidenceError
from resolve_ib_artifact import resolve
from ci_workflow_contract import load_workflow


class ArtifactReuseTests(unittest.TestCase):
    def setUp(self):
        self.artifact=dict(id=12,expired=False,workflow_run=dict(id=13,head_sha='a'*40,head_branch='main'))
        self.run=dict(event='workflow_dispatch',head_sha='a'*40,path='.github/workflows/ib-paper-qualification.yml',
                      actor=dict(id=102159240),triggering_actor=dict(login='ProfHepta'))
        self.jobs=dict(jobs=[dict(name='ib-paper-candidate-artifact-build',conclusion='success')])
    def fetch(self,path):
        return self.artifact if '/artifacts/' in path else self.jobs if '/jobs?' in path else self.run
    def test_previous_run_artifact_is_selected_without_rebuild(self):self.assertEqual(resolve('12','a'*40,self.fetch)['run_id'],13)
    def test_campaign_failure_does_not_invalidate_successful_build(self):
        self.run['conclusion']='failure';self.assertEqual(resolve('12','a'*40,self.fetch)['artifact_id'],12)
    def test_expired_or_substituted_artifact_rejected(self):
        self.artifact['expired']=True
        with self.assertRaises(EvidenceError):resolve('12','a'*40,self.fetch)
    def test_unknown_builder_cannot_supply_artifact(self):
        self.jobs['jobs'][0]['conclusion']='failure'
        with self.assertRaises(EvidenceError):resolve('12','a'*40,self.fetch)
    def test_untrusted_source_or_actor_rejected(self):
        for key,value in [('event','pull_request'),('head_sha','b'*40),('actor',{'id':1})]:
            with self.subTest(key=key):
                old=self.run[key];self.run[key]=value
                with self.assertRaises(EvidenceError):resolve('12','a'*40,self.fetch)
                self.run[key]=old
    def test_new_build_has_no_broker_mutation_opt_in(self):
        workflow=load_workflow(ROOT/'.github/workflows/ib-paper-qualification.yml')
        self.assertEqual(workflow['on']['workflow_dispatch']['inputs']['rollout_stage']['default'],'build')
        self.assertNotIn('mutation_mode',workflow['jobs']['build-candidate']['if'])
        self.assertIn('inputs.candidate_sha == github.sha',workflow['jobs']['build-candidate']['if'])
        self.assertNotIn('inputs.candidate_sha == github.sha',workflow['jobs']['campaign']['if'])

if __name__=='__main__':unittest.main()
