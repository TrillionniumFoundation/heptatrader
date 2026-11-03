import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import continue_current100k_new_scope as continuation
from laya_cell.financial_value_profile import CENTERED_RESIDUAL_PROFILE


class GuardedFitResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.request = {'controller_root': str(self.root / 'control'), 'fit_timeout_seconds': 60}
        Path(self.request['controller_root']).mkdir()
        self.output = self.root / 'fit-output'
        self.config = {'output_root': str(self.output)}
        self.stage = {'config_sha256': 'a'*64, 'launch_argv': ['/pinned/python', '-B', '/pinned/launch.py'],
            'release': '/pinned/runtime'}
        self.readiness = {'dataset_sha256': 'b'*64}
        self.addCleanup(patch.stopall)
        self.completed = patch.object(continuation, 'completed_candidate', return_value=None).start()
        self.child = patch.object(continuation, 'child').start()
        self.child.return_value = subprocess.CompletedProcess(self.stage['launch_argv'], 1, '',
            'Traceback (most recent call last):\nhistorical_training.common.EvidenceError: RESIDENT_ADMISSION_DENIED_NO_MODEL_LOADED\n')

    def run_fit(self):
        return continuation.guarded_fit(self.request, self.stage, self.config, self.readiness)

    def test_only_explicit_pre_model_guard_denial_can_retry(self):
        self.assertEqual(self.run_fit(), (None, 'WAITING_ORIGINAL_SHARED_GUARD_NO_MODEL_LOADED'))
        self.assertEqual(self.run_fit(), (None, 'WAITING_ORIGINAL_SHARED_GUARD_NO_MODEL_LOADED'))
        self.assertEqual(self.child.call_count, 2)
        attempts = list((Path(self.request['controller_root']) / 'fit-attempts').glob('*/result.json'))
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(json.loads(path.read_bytes())['outcome'] == 'GUARD_DENIED_NO_MODEL_OR_OUTPUT'
                            for path in attempts))
        self.assertFalse(self.output.exists())

    def test_unknown_result_never_relaunches_even_without_output(self):
        self.child.side_effect = subprocess.TimeoutExpired('fit', 60)
        with self.assertRaises(subprocess.TimeoutExpired): self.run_fit()
        self.child.side_effect = None
        with self.assertRaisesRegex(ValueError, 'FIT_EFFECT_UNKNOWN_NO_AUTOMATIC_RETRAINING'):
            self.run_fit()
        self.assertEqual(self.child.call_count, 1)

    def test_partial_output_never_relaunches(self):
        self.output.mkdir(); (self.output / 'plan.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'PARTIAL_FIT_OUTPUT_NO_AUTOMATIC_RETRAINING'):
            self.run_fit()
        self.child.assert_not_called()

    def test_guard_error_with_output_is_not_classified_as_safe_retry(self):
        def unexpected(*_):
            self.output.mkdir()
            return subprocess.CompletedProcess([], 1, '', 'EvidenceError: RESIDENT_ADMISSION_DENIED_NO_MODEL_LOADED')
        self.child.side_effect = unexpected
        with self.assertRaisesRegex(ValueError, 'FIT_FAILED_NO_AUTOMATIC_RETRAINING'): self.run_fit()
        with self.assertRaisesRegex(ValueError, 'PARTIAL_FIT_OUTPUT_NO_AUTOMATIC_RETRAINING'): self.run_fit()
        self.assertEqual(self.child.call_count, 1)

    def test_success_without_candidate_cannot_retry(self):
        self.child.return_value = subprocess.CompletedProcess([], 0, '{}', '')
        with self.assertRaisesRegex(ValueError, 'FIT_FAILED_NO_AUTOMATIC_RETRAINING'): self.run_fit()
        with self.assertRaisesRegex(ValueError, 'FIT_FAILED_NO_AUTOMATIC_RETRAINING'): self.run_fit()
        self.assertEqual(self.child.call_count, 1)

    def test_completed_candidate_resolves_unknown_result_without_retraining(self):
        self.child.side_effect = subprocess.TimeoutExpired('fit', 60)
        with self.assertRaises(subprocess.TimeoutExpired): self.run_fit()
        candidate = {'candidate_id': 'verified'}; self.completed.return_value = candidate
        self.assertEqual(self.run_fit(), (candidate, 'COMPLETED_FIT_REUSED_NO_RETRAINING'))
        self.assertEqual(self.child.call_count, 1)

    def test_normal_success_requires_independent_candidate_verification(self):
        candidate = {'candidate_id': 'verified'}
        self.completed.side_effect = [None, candidate]
        self.child.return_value = subprocess.CompletedProcess([], 0, '{}', '')
        self.assertEqual(self.run_fit(), (candidate, 'GUARDED_FIT_COMPLETED'))
        self.assertEqual(self.completed.call_count, 2)


class PreparationSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.repo = self.root / 'executor-source'; self.repo.mkdir()
        self.history_path = self.root / 'history/plans/old-plan/plan.json'
        self.prior = {'trial': 1, 'plan_id': 'old-plan'}
        self.write(self.history_path, self.prior)
        self.consumer = {'registration': {'manifest': '/producer/manifest.json', 'sha256': 'b'*64},
            'output_root': str(self.root / 'consumer-output')}
        self.consumer_path = self.root / 'consumer.json'; self.write(self.consumer_path, self.consumer)
        self.prep = {'schema': 'heptatrader.current100k-newscope-preparation.v1',
            'selection_policy': continuation.POLICY, 'minimum_rows': 32, 'registration_scope': 'NEW_PRODUCER_SCOPE',
            'source_commit': 'original-preparation-commit', 'source_repository': '/original/changed-repository',
            'consumer_config': str(self.consumer_path), 'consumer_config_sha256': self.sha(self.consumer_path),
            'producer_registration': self.consumer['registration'], 'expected_trial': 2, 'prior_plan_id': 'old-plan',
            'legacy_plans_sha256': {str(self.history_path): self.sha(self.history_path)}}
        self.prep_path = self.root / 'original-preparation/PREPARATION_REQUEST.json'; self.write(self.prep_path, self.prep)
        self.owner = {'hip_runtime': {'sha256': 'accepted'}, 'python': '/original/python', 'base_directory': '/original/base'}
        self.template = {'schema': 'heptatrader.current100k-value-trial-config.v1',
            'readout_profile': CENTERED_RESIDUAL_PROFILE, 'max_rows': 64, 'steps': 16, 'scale_usd': 100,
            'actor_update': False, 'automatic_selection': False, 'trade_authority': False,
            'output_root': str(self.root / 'old-fit'), **self.owner}
        template_path = self.root / 'fit-template.json'; self.write(template_path, self.template)
        prior_stage_path = self.root / 'prior-stage.json'
        self.write(prior_stage_path, {'registration': {'plan_path': str(self.history_path), 'plan_sha256': self.sha(self.history_path)}})
        guard_path = self.root / 'guard.json'; self.write(guard_path, {'schema': 'fixed-guard'})
        self.request = {'schema': continuation.SCHEMA, 'automatic_training': True,
            'automatic_normal_registration_activation': True, 'trade_authority': False, 'old_trials_modified': False,
            'source_repository': str(self.repo), 'source_commit': 'new-executor-commit',
            'preparation_request': str(self.prep_path), 'preparation_request_sha256': self.sha(self.prep_path),
            'preparation_source_commit': self.prep['source_commit'],
            'prior_forward_stage': str(prior_stage_path), 'prior_forward_stage_sha256': self.sha(prior_stage_path),
            'fit_template': str(template_path), 'fit_template_sha256': self.sha(template_path),
            'guard_config': str(guard_path), 'guard_config_sha256': self.sha(guard_path),
            'fit_timeout_seconds': 600, 'activation_readmission_seconds': 120, 'activation_events': 120,
            'controller_root': str(self.root / 'controller'), 'fit_output_root': str(self.root / 'new-fit'),
            'forward_root': str(self.root / 'new-forward')}
        self.addCleanup(patch.stopall)
        patch.object(continuation, 'source_check', return_value=self.repo).start()
        patch('historical_training.current100k.validate_config').start()
        patch('historical_training.resident_resource_guard.verify_config', return_value=self.owner).start()
        patch('laya_cell.financial_forward_protocol.validate_plan').start()

    @staticmethod
    def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(continuation.canonical(value)+b'\n')

    def test_new_executor_is_bound_separately_and_never_rewrites_preparation(self):
        before = self.prep_path.read_bytes()
        inputs = continuation.load_inputs(self.request)
        self.assertEqual(inputs['prep']['source_commit'], 'original-preparation-commit')
        self.assertEqual(self.request['source_commit'], 'new-executor-commit')
        self.assertEqual(self.prep_path.read_bytes(), before)

    def test_changed_preparation_bytes_or_identity_refuse(self):
        self.request['preparation_source_commit'] = 'rewritten-origin'
        with self.assertRaisesRegex(ValueError, 'PREPARATION_SOURCE_CHANGED'):
            continuation.load_inputs(self.request)
        self.request['preparation_source_commit'] = self.prep['source_commit']
        self.prep_path.write_bytes(self.prep_path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError, 'INPUT_CHANGED'):
            continuation.load_inputs(self.request)

    def test_history_growth_cannot_allocate_next_trial_automatically(self):
        self.write(self.history_path.parent.parent / 'another/plan.json', {'trial': 2})
        with self.assertRaisesRegex(ValueError, 'HISTORY_CHANGED_NO_REALLOCATION'):
            continuation.load_inputs(self.request)

    def test_new_outputs_cannot_overlap_old_evidence_or_each_other(self):
        for value in (str(self.prep_path.parent), str(self.root), str(self.root / 'controller/inside')):
            candidate = {**self.request, 'fit_output_root': value}
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'OUTPUT_ROOTS_REQUIRED'):
                continuation.load_inputs(candidate)

    def test_guard_keeps_original_python_and_accepted_hip(self):
        self.owner['python'] = '/replacement/python'
        with self.assertRaisesRegex(ValueError, 'ACCEPTED_MODEL_PYTHON_HIP_CHANGED'):
            continuation.load_inputs(self.request)

    def test_timer_persists_beyond_one_day_and_stops_execution_when_complete(self):
        units = continuation.service_units(self.root / 'request.json', 'a'*64, self.request)
        service = next(value for key, value in units.items() if key.endswith('.service'))
        timer = next(value for key, value in units.items() if key.endswith('.timer'))
        self.assertIn('OnCalendar=*-*-* *:*:00', timer); self.assertIn('Persistent=true', timer)
        self.assertNotIn('OnUnitActiveSec=', timer); self.assertNotIn('--duration-seconds', service)
        self.assertIn('ConditionPathExists=!' + str(self.root / 'controller/CONTINUATION_COMPLETE.json'), service)
        self.assertIn('KillMode=control-group', service); self.assertIn('Restart=no', service)
        self.assertIn(str(self.repo / 'scripts/continue_current100k_new_scope.py'), service)
        self.assertNotIn('heptatrader-paper-driver', service); self.assertNotIn('heptatrader-gateway', service)


class PreparationLockTests(unittest.TestCase):
    def test_live_original_watcher_blocks_second_cohort_selector(self):
        import fcntl
        import os
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); path = output / 'preparation.lock'
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = continuation.ready({}, {'prep': {}, 'prep_path': output / 'PREPARATION_REQUEST.json'})
                self.assertEqual(result, (None, {'state': 'WAITING_ORIGINAL_PREPARATION_WATCHER_LOCK'}))
                self.assertEqual(list(output.iterdir()), [path])
            finally: os.close(fd)


class ActivationHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.forward = self.root / 'forward'; self.forward.mkdir()
        self.request = {'controller_root': str(self.root), 'forward_root': str(self.forward),
            'activation_readmission_seconds': 120, 'activation_events': 120,
            'source_commit': 'executor', 'preparation_source_commit': 'original'}
        self.registration = self.root / 'REGISTRATION_REQUEST.json'
        continuation.write_once(self.registration, {'registration_not_before_ms': 1000000})
        self.stage = {'release': '/new/runtime', 'registration_request': str(self.registration),
            'registration_request_sha256': continuation.sha(self.registration), 'expected_trial': 5,
            'registration_precheck_argv': ['/pinned/registrar', '--registration-precheck'],
            'candidate_id': 'candidate'}
        self.stage_path = self.root / 'WARM_STAGE.json'; continuation.write_once(self.stage_path, self.stage)
        self.deadline = 1000000 + 119*120*1000 + 1000
        selection = self.root / 'SELECTION_REQUEST.json'
        continuation.write_once(selection, {'activation_deadline_ms': self.deadline})
        wake_root = self.root / 'registration-wake'; wake_root.mkdir()
        self.wake = {'stage_sha256': continuation.sha(self.stage_path),
            'selection_request_sha256': continuation.sha(selection), 'deadline_ms': self.deadline,
            'calendar_events': 120, 'mode': 'register',
            'service_name': 'heptatrader-current100k-registration-register-abc.service',
            'timer_name': 'heptatrader-current100k-registration-register-abc.timer',
            'service': 'same-plan activation', 'timer': 'finite wake'}
        continuation.write_once(wake_root / 'WAKE_STAGE.json', self.wake)
        self.addCleanup(patch.stopall)
        patch.object(continuation.time, 'time', return_value=2000).start()
        self.install = patch.object(continuation, 'install_units').start()
        self.observer = patch.object(continuation, 'plan_observer', return_value=({'service': 'reader'}, True)).start()
        self.child = patch.object(continuation, 'child', return_value=subprocess.CompletedProcess([], 0,
            json.dumps({'state': 'REGISTERED_NATIVE_EXECUTION_LIVE_VERIFIED', 'plan_id': 'new-plan', 'trial': 5,
                'at_ms': 2000000, 'role_configs_verified': True,
                'timers_enabled_active_and_next_event_verified': True}), '')).start()

    def activate(self):
        plan = self.forward / 'plan.json'; continuation.write_once(plan, {'plan_id': 'new-plan'})
        continuation.write_once(self.forward / 'activation-status.json', {
            'state': 'SAME_PLAN_NATIVE_WARM_EXECUTION_SCHEDULED', 'plan_id': 'new-plan',
            'plan_path': str(plan), 'activation_before_input_start': True})

    def run_handoff(self):
        return continuation.handoff(self.request, self.stage_path, self.stage)

    def test_install_and_wait_do_not_claim_trial_or_prediction_success(self):
        result = self.run_handoff()
        self.assertEqual(result['state'], 'WAITING_NORMAL_FINITE_REGISTRATION_AND_ACTIVATION')
        self.assertIsNone(result['trial_registered']); self.install.assert_called_once()
        self.child.assert_not_called(); self.observer.assert_not_called()
        self.assertFalse((self.root / 'CONTINUATION_COMPLETE.json').exists())
        self.run_handoff(); self.install.assert_called_once()

    def test_registered_and_deployed_is_distinct_from_empirical_preopen_success(self):
        self.activate(); result = self.run_handoff()
        self.assertEqual(result['state'], 'VERIFIED_NEW_SCOPE_NATIVE_WARM_EXECUTION_SCHEDULED')
        self.assertFalse(result['current100k_p0_closed'])
        self.assertFalse(result['same_request_preopen_prediction_chain_verified'])
        self.assertTrue(result['first_flat_request_still_requires_cold_model_start'])
        self.assertTrue(result['training_fit_is_not_efficacy'])
        self.assertTrue(result['role_configs_verified'])
        self.assertTrue(result['timers_enabled_active_and_next_event_verified'])
        self.assertEqual(result['plan_id'], 'new-plan'); self.observer.assert_called_once()
        self.assertEqual(json.loads((self.root / 'CONTINUATION_COMPLETE.json').read_bytes()), result)

    def test_standby_profile_deployment_never_claims_observed_standby_or_preopen_success(self):
        self.request['worker_profile'] = self.stage['worker_profile'] = 'cpu-standby-v2'
        self.request['worker_profile_evidence'] = {'report': {'path': '/pinned/report.json', 'sha256': 'a'*64},
            'lifecycle': {'path': '/pinned/lifecycle.json', 'sha256': 'b'*64}}
        self.stage_path.chmod(0o600); self.stage_path.write_bytes(continuation.canonical(self.stage)+b'\n')
        self.wake['stage_sha256'] = continuation.sha(self.stage_path)
        wake_path = self.root / 'registration-wake/WAKE_STAGE.json'
        wake_path.chmod(0o600); wake_path.write_bytes(continuation.canonical(self.wake)+b'\n')
        self.activate(); result = self.run_handoff()
        self.assertEqual(result['worker_profile'], 'cpu-standby-v2')
        self.assertEqual(result['worker_profile_evidence'], self.request['worker_profile_evidence'])
        self.assertFalse(result['current100k_p0_closed'])
        self.assertFalse(result['same_request_preopen_prediction_chain_verified'])
        self.assertIsNone(result['first_flat_request_still_requires_cold_model_start'])
        self.assertIsNone(result['cpu_standby_observed_before_first_request'])
        self.assertEqual(result['remaining_timing_risk'],
            'CPU_STANDBY_AND_FRESH_ADMISSION_REQUIRE_ORIGINAL_REQUEST_OBSERVATION')

    def test_unverified_observer_does_not_complete_deployment_acceptance(self):
        self.activate(); self.observer.return_value = ({'service': 'reader'}, False)
        result = self.run_handoff()
        self.assertEqual(result['state'], 'REGISTERED_AND_SCHEDULED_WAITING_READ_ONLY_OBSERVER')
        self.assertFalse((self.root / 'CONTINUATION_COMPLETE.json').exists())

    def test_registrar_reverification_must_match_same_plan(self):
        self.activate()
        self.child.return_value = subprocess.CompletedProcess([], 0, json.dumps({
            'state': 'REGISTERED_NATIVE_EXECUTION_LIVE_VERIFIED', 'plan_id': 'another', 'trial': 5}), '')
        with self.assertRaisesRegex(ValueError, 'ACTIVATED_PLAN_IDENTITY_CHANGED'): self.run_handoff()
        self.observer.assert_not_called()

    def test_elapsed_input_window_cannot_be_accepted_as_live_deployment(self):
        self.activate()
        self.child.return_value = subprocess.CompletedProcess([], 0, json.dumps({
            'state': 'REGISTERED_INPUT_WINDOW_ELAPSED_NO_LIVE_ACCEPTANCE',
            'plan_id': 'new-plan', 'trial': 5, 'current100k_p0_closed': False}), '')
        result = self.run_handoff()
        self.assertEqual(result['state'], 'REGISTERED_INPUT_WINDOW_ELAPSED_NO_LIVE_ACCEPTANCE')
        self.assertFalse((self.root / 'CONTINUATION_COMPLETE.json').exists())
        self.observer.assert_not_called()

    def test_expired_registration_calendar_is_never_renewed(self):
        patch.object(continuation.time, 'time', return_value=self.deadline/1000+1).start()
        before = (self.root / 'SELECTION_REQUEST.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'ACTIVATION_DEADLINE_EXPIRED_NO_RENEWAL'): self.run_handoff()
        self.install.assert_not_called()
        self.assertEqual((self.root / 'SELECTION_REQUEST.json').read_bytes(), before)

    def test_bound_warm_stage_resumes_without_readiness_or_fit_repetition(self):
        (self.root / 'warm-stage').mkdir(); path = self.root / 'warm-stage/WARM_STAGE.json'
        continuation.write_once(path, self.stage)
        continuation.write_once(self.root / 'WARM_STAGE_BOUND.json', {'path': str(path), 'sha256': continuation.sha(path)})
        with patch.object(continuation, 'ready') as ready, patch.object(continuation, 'guarded_fit') as fit, \
                patch.object(continuation, 'handoff', return_value={'state': 'pending'}) as handoff:
            self.assertEqual(continuation.step(self.request, {}), {'state': 'pending'})
        ready.assert_not_called(); fit.assert_not_called(); handoff.assert_called_once()


if __name__ == '__main__':
    unittest.main()
