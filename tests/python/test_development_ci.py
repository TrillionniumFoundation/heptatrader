from pathlib import Path
import unittest, yaml
ROOT = Path(__file__).resolve().parents[2]

def workflow(name):
    return yaml.load((ROOT/'.github/workflows'/name).read_text(), Loader=yaml.BaseLoader)

class DevelopmentCiTests(unittest.TestCase):
    def test_core_runs_actual_tests_not_status_polling(self):
        ci=workflow('core-ci.yml'); self.assertEqual(set(ci['jobs']), {'core'})
        self.assertEqual(ci['permissions'], {'contents':'read'})
        self.assertEqual(set(ci['on']), {'push','pull_request','workflow_dispatch'})
        steps=ci['jobs']['core']['steps']; commands='\n'.join(x.get('run','') for x in steps)
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
