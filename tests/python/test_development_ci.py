from pathlib import Path
import unittest, yaml
ROOT = Path(__file__).resolve().parents[2]

def workflow(name):
    return yaml.load((ROOT/'.github/workflows'/name).read_text(), Loader=yaml.BaseLoader)

class DevelopmentCiTests(unittest.TestCase):
    def test_core_runs_actual_tests_not_status_polling(self):
        ci=workflow('core-ci.yml'); self.assertEqual(set(ci['jobs']), {'core', 'python'})
        self.assertEqual(ci['permissions'], {'contents':'read'})
        self.assertEqual(set(ci['on']), {'push','pull_request','workflow_dispatch'})
        steps=[step for job in ci['jobs'].values() for step in job['steps']]; commands='\n'.join(x.get('run','') for x in steps)
        self.assertIn('./scripts/dev_core.sh',commands)
        for lane in ('core','source'):
            self.assertEqual(commands.count('scripts/run_python_tests.py --lane '+lane),1)
        for obsolete in ('verify_required_checks','accept_core_release','ci_change_scope','--write-module-metadata'):
            self.assertNotIn(obsolete,commands)
    def test_deep_suites_are_manual_not_deleted(self):
        for name in ('canonical-full-suite.yml','heptadll-integration.yml'):
            ci=workflow(name);self.assertEqual(set(ci['on']),{'workflow_dispatch'})
            self.assertTrue(ci['jobs'])
    def test_release_and_broker_qualification_remain_separate(self):
        ci=workflow('release.yml');self.assertIn('tags',ci['on']['push'])
        self.assertIn('scripts/accept_core_release.py',str(ci))
        self.assertTrue((ROOT/'.github/workflows/ib-paper-qualification.yml').is_file())
    def test_no_cross_workflow_approval_runtime(self):
        for name in ('verify_required_checks.py','plan_owner_ruleset.py'):
            self.assertFalse((ROOT/'scripts'/name).exists())


class StackedCiReachabilityTests(unittest.TestCase):
    def test_every_pr_base_can_trigger_without_owner_or_draft_gate(self):
        ci = workflow('core-ci.yml')
        self.assertNotIn('branches', ci['on']['pull_request'])
        self.assertNotIn('branches-ignore', ci['on']['pull_request'])
        for event in ('opened', 'synchronize', 'reopened', 'ready_for_review'):
            self.assertIn(event, ci['on']['pull_request']['types'])
        for job in ci['jobs'].values():
            self.assertNotIn('if', job)
            self.assertNotIn('needs', job)
            self.assertEqual(job['runs-on'], 'ubuntu-24.04')
            checkout = next(x for x in job['steps'] if x.get('name') ==
                            'Checkout exact event subject without credentials')
            self.assertEqual(checkout['with']['persist-credentials'], 'false')
            self.assertIn('github.event.pull_request.head.sha', checkout['with']['ref'])

    def test_native_feedback_does_not_wait_for_torch_or_python(self):
        ci = workflow('core-ci.yml')
        native = str(ci['jobs']['core'])
        self.assertNotIn('torch', native)
        self.assertIn('./scripts/dev_core.sh', native)
        self.assertIn('torch==2.8.0', str(ci['jobs']['python']))
        self.assertNotIn('pull_request_target', ci['on'])
