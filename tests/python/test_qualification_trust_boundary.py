from __future__ import annotations
import copy
from pathlib import Path
import sys
import tempfile
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from ci_workflow_contract import load_workflow
import check_qualification_trust_boundary as contract


class QualificationBoundaryTests(unittest.TestCase):
    def mutate(self,mutation):
        value=load_workflow(ROOT/contract.WORKFLOW);mutation(value)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/contract.WORKFLOW;path.parent.mkdir(parents=True)
            path.write_text(yaml.safe_dump(value,sort_keys=False));return contract.validate(root)
    def test_repository_executable_contract(self):self.assertEqual(contract.validate(),[])
    def test_untrusted_actor_or_branch_cannot_allocate_host(self):
        for name in ('build-candidate','resolve-artifact','campaign'):
            with self.subTest(name=name):self.assertTrue(self.mutate(lambda w:w['jobs'][name].update({'if':'true'})))
    def test_build_not_a_mutation_environment(self):self.assertTrue(self.mutate(lambda w:w['jobs']['build-candidate'].update(environment='ib-paper')))
    def test_campaign_needs_environment_and_exact_artifact_admission(self):
        for field in ('environment','needs'):
            with self.subTest(field=field):self.assertTrue(self.mutate(lambda w:w['jobs']['campaign'].pop(field)))
    def test_fake_comment_does_not_supply_host_execution(self):
        def mutate(w):
            for step in w['jobs']['campaign']['steps']:
                if 'hepta_paper_rollout_host.py' in step.get('run',''):step['run']='# '+step['run'].replace('\n','\n# ')
        self.assertTrue(self.mutate(mutate))
    def test_step_failure_cannot_be_suppressed(self):self.assertTrue(self.mutate(lambda w:w['jobs']['campaign']['steps'][2].update({'continue-on-error':True})))
    def test_mutation_step_cannot_skip_preflight(self):self.assertTrue(self.mutate(lambda w:w['jobs']['campaign']['steps'][3].update({'if':'false'})))
    def test_success_only_upload_is_rejected(self):
        def mutate(w):
            for step in w['jobs']['campaign']['steps']:
                if str(step.get('uses','')).startswith('actions/upload-artifact@'):step.pop('if')
        self.assertTrue(self.mutate(mutate))
    def test_download_is_not_tied_to_current_run_attempt(self):
        def mutate(w):
            for step in w['jobs']['campaign']['steps']:
                if str(step.get('uses','')).startswith('actions/download-artifact@'):step['with']['artifact-ids']='${{ github.run_attempt }}'
        self.assertTrue(self.mutate(mutate))
    def test_actions_are_digest_pinned(self):self.assertTrue(self.mutate(lambda w:w['jobs']['campaign']['steps'][0].update(uses='actions/checkout@main')))

if __name__=='__main__':unittest.main()
