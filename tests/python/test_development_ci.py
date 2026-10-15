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

    def test_cpu_torch_download_cache_never_replaces_exact_head_tests(self):
        ci = workflow('core-ci.yml')
        python_steps = ci['jobs']['python']['steps']
        caches = [step for step in python_steps if
                  step.get('name') == 'Cache CPU research dependency downloads']
        self.assertEqual(len(caches), 1)
        cache = caches[0]
        self.assertEqual(cache['uses'],
                         'actions/cache@0400d5f644dc74513175e3cd8d07132dd4860809')
        self.assertEqual(cache['with']['path'], '~/.cache/pip')
        self.assertIn('cpu-torch-2.8.0', cache['with']['key'])
        self.assertNotIn('restore-keys', cache['with'])
        self.assertEqual(cache.get('continue-on-error'), 'true')
        cache_index = python_steps.index(cache)
        install_index = next(i for i, step in enumerate(python_steps) if
                             step.get('name') == 'Install pinned CPU research test dependency')
        run_index = next(i for i, step in enumerate(python_steps) if
                         step.get('name') == 'Python behavior and source tests once')
        self.assertLess(cache_index, install_index)
        self.assertLess(install_index, run_index)
        self.assertNotIn('if', python_steps[install_index])
        self.assertNotIn('if', python_steps[run_index])
        self.assertIn('torch==2.8.0', python_steps[install_index]['run'])
        self.assertIn('torch.version.cuda is None', python_steps[install_index]['run'])
        self.assertFalse(any('actions/cache@' in str(x) for x in ci['jobs']['core']['steps']))

    def test_native_feedback_does_not_wait_for_torch_or_python(self):
        ci = workflow('core-ci.yml')
        native = str(ci['jobs']['core'])
        self.assertNotIn('torch', native)
        self.assertIn('./scripts/dev_core.sh', native)
        self.assertIn('torch==2.8.0', str(ci['jobs']['python']))
        self.assertNotIn('pull_request_target', ci['on'])


class CheckoutTransportTests(unittest.TestCase):
    def test_transport_setup_precedes_exact_checkout_in_each_job(self):
        for job in workflow('core-ci.yml')['jobs'].values():
            steps=job['steps']
            self.assertEqual(steps[0]['run'], 'git config --global http.version HTTP/1.1')
            self.assertEqual(steps[1]['name'], 'Checkout exact event subject without credentials')
            self.assertNotIn('continue-on-error', steps[1])


class CurrentHeadFeedbackTests(unittest.TestCase):
    def test_superseded_push_and_pr_feedback_can_cancel_but_manual_stays(self):
        ci=workflow('core-ci.yml')
        self.assertEqual(ci['concurrency']['cancel-in-progress'],
                         "${{ github.event_name != 'workflow_dispatch' }}")
        self.assertIn('github.event_name',ci['concurrency']['group'])
        self.assertEqual(workflow('ib-paper-qualification.yml')['concurrency']['cancel-in-progress'],'false')
