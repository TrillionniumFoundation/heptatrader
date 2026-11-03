"""Finite registration observation and wake boundaries; private fixtures only."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import precheck_current100k_registration as precheck


def raw(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


class ProducerCheckTests(unittest.TestCase):
    def setUp(self):
        self.registration = {'manifest': '/original/manifest.json', 'sha256': 'a' * 64}
        self.config = {'producer_registration': self.registration, 'period_sha256': 'b' * 64}
        self.prior = {'behavior_scope': {'behavior_model_id': 'original-model'}}
        self.current = {'manifest': self.registration['manifest'], 'manifest_sha256': self.registration['sha256'],
            'release': '/original', 'pid_key': ('123', '456'), 'status': {'InvocationID': 'invocation'}, 'runtime_sha256': {'driver.py': 'c' * 64}}
        self.driver = types.ModuleType('activate_prepared_driver')
        self.driver.running_driver = Mock(return_value=self.current)
        self.driver.same_driver = Mock()
        self.items = {'request-1': {'request': {'request_id': 'request-1', 'started_at_ms': 1000,
            'position_at_request': 0, 'model_id': 'original-model'}, 'request_sha256': 'd' * 64,
            'producer_manifest_sha256': self.registration['sha256']}}
        self.snapshot = Mock()
        self.snapshot.read.return_value = ({}, self.items, [], [])
        self.source = types.ModuleType('laya_cell.financial_forward_source')
        self.source.VerifiedIngressSnapshot = Mock(return_value=self.snapshot)
        modules = patch.dict(sys.modules, {'activate_prepared_driver': self.driver,
            'laya_cell.financial_forward_source': self.source})
        modules.start(); self.addCleanup(modules.stop)
        clock = patch.object(precheck.time, 'time', return_value=2.0)
        clock.start(); self.addCleanup(clock.stop)
        for name, kwargs in [('check_precheck_source', {}), ('registration_state', {'return_value': {'state': 'EMPTY', 'plan': None}})]:
            item = patch.object(precheck, name, **kwargs); item.start(); self.addCleanup(item.stop)

    def test_exact_running_producer_and_real_request_observed_without_future_sample(self):
        proof = precheck.running_producer(self.config, self.prior)
        self.assertEqual(proof['pid_key'], ['123', '456'])
        self.assertEqual(proof['request_activity']['request_id'], 'request-1')
        self.assertFalse(proof['request_activity']['future_trial_sample'])
        self.assertEqual(self.driver.running_driver.call_count, 2)
        self.driver.same_driver.assert_called_once_with(self.current, self.current)

    def test_live_producer_drift_refuses_before_ingress(self):
        self.driver.running_driver.return_value = {**self.current, 'manifest_sha256': 'e' * 64}
        with self.assertRaisesRegex(ValueError, 'RUNNING_PRODUCER_CHANGED'):
            precheck.running_producer(self.config, self.prior)
        self.snapshot.read.assert_not_called()

    def test_process_change_during_source_read_cannot_pass(self):
        self.driver.same_driver.side_effect = RuntimeError('DRIVER_PROCESS_CHANGED')
        with self.assertRaisesRegex(RuntimeError, 'DRIVER_PROCESS_CHANGED'):
            precheck.running_producer(self.config, self.prior)

    def test_source_provenance_issue_refuses_even_with_a_fresh_request(self):
        self.snapshot.read.return_value = ({}, self.items, [], [{'reason': 'gap'}])
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_SOURCE_UNAVAILABLE'):
            precheck.running_producer(self.config, self.prior)

    def test_stopped_driver_refuses(self):
        self.driver.running_driver.side_effect = RuntimeError('DRIVER_NOT_ACTIVE')
        with self.assertRaisesRegex(RuntimeError, 'DRIVER_NOT_ACTIVE'):
            precheck.running_producer(self.config, self.prior)
        self.snapshot.read.assert_not_called()

    def test_original_strict_sixty_second_freshness_boundary(self):
        proof = precheck.fresh_original_request(self.items, self.registration, 'original-model', 60999)
        self.assertEqual(proof['age_ms'], 59999)
        for now in (61000, 999):
            with self.subTest(now=now), self.assertRaisesRegex(ValueError, 'NO_FRESH_ORIGINAL'):
                precheck.fresh_original_request(self.items, self.registration, 'original-model', now)

    def test_nonflat_or_different_model_or_producer_is_not_activity_proof(self):
        for key, value in [('position_at_request', 1), ('model_id', 'new-model')]:
            items = copy.deepcopy(self.items); items['request-1']['request'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'NO_FRESH_ORIGINAL'):
                precheck.fresh_original_request(items, self.registration, 'original-model', 2000)
        items = copy.deepcopy(self.items); items['request-1']['producer_manifest_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'NO_FRESH_ORIGINAL'):
            precheck.fresh_original_request(items, self.registration, 'original-model', 2000)

    def test_no_fresh_input_does_not_create_plan_or_trial_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'future'; c = {**self.config, 'forward_root': str(root)}
            with patch.object(precheck, 'registration_inputs', return_value=({'expected_trial': 5,
                    'registration_not_before_ms': 1}, self.prior)):
                self.snapshot.read.return_value = ({}, {}, [], [])
                with self.assertRaisesRegex(ValueError, 'NO_FRESH_ORIGINAL'):
                    precheck.precheck_registration(c, Path('/request.json'), 'a' * 64)
            self.assertFalse(root.exists())

    def test_known_published_result_needs_no_new_request_or_write(self):
        request = {'registration_not_before_ms': 1}
        with patch.object(precheck, 'registration_inputs', return_value=(request, self.prior)),                 patch.object(precheck, 'registration_state', return_value={'state': 'PUBLISHED', 'plan': {'plan_id': 'original', 'trial': 5}}),                 patch.object(precheck, 'running_producer') as observer:
            _, proof = precheck.precheck_registration({}, Path('/request'), 'a' * 64)
        self.assertEqual(proof['state'], 'REGISTRATION_ALREADY_COMPLETE_VERIFIED_NO_WRITE')
        observer.assert_not_called(); self.assertFalse(proof['registration_performed'])
    def test_unknown_prepared_result_refuses_without_running_producer_or_effects(self):
        with patch.object(precheck, 'registration_inputs', return_value=({}, self.prior)),                 patch.object(precheck, 'registration_state', side_effect=ValueError('REGISTRATION_UNKNOWN')),                 patch.object(precheck, 'running_producer') as observer:
            with self.assertRaisesRegex(ValueError, 'REGISTRATION_UNKNOWN'):
                precheck.precheck_registration({}, Path('/request'), 'a' * 64)
        observer.assert_not_called()
    def test_clock_rollback_cannot_cross_original_not_before_boundary(self):
        request = {'registration_not_before_ms': 1500, 'expected_trial': 5}
        with patch.object(precheck, 'registration_inputs', return_value=(request, self.prior)),                 patch.object(precheck, 'running_producer', return_value={'request_activity': {'started_at_ms': 900}}),                 patch.object(precheck.time, 'time', side_effect=[2.0, 1.499]):
            with self.assertRaisesRegex(ValueError, 'WAIT_PRIOR_OUTCOME_END'):
                precheck.precheck_registration({}, Path('/request'), 'a' * 64)
    def test_wake_expiry_during_observation_does_not_grant_eligibility(self):
        with patch.object(precheck, 'registration_inputs', return_value=({}, self.prior)), \
                patch.object(precheck, 'running_producer', return_value={'request_activity': {'started_at_ms': 900}}), \
                patch.object(precheck.time, 'time', side_effect=[1.0, 2.0]):
            with self.assertRaisesRegex(ValueError, 'WAKE_DEADLINE_EXPIRED'):
                precheck.precheck_registration({}, Path('/request'), 'a' * 64, wake_deadline_ms=1500)


class ImportedSourcePinTests(unittest.TestCase):
    def fixture(self, root):
        mapping = {name[:-3]: 'scripts/' + name for name in precheck.SUPPORT_FILES}
        mapping.update({
            'historical_training.common': 'research/historical_training/common.py',
            'historical_training.current100k': 'research/historical_training/current100k.py',
            'historical_training.current100k_value_trial': 'research/historical_training/current100k_value_trial.py',
            'laya_cell.financial_forward_protocol': 'learn/laya_cell/financial_forward_protocol.py',
            'laya_cell.current100k_warm_flat': 'learn/laya_cell/current100k_warm_flat.py',
            'laya_cell.financial_forward_source': 'learn/laya_cell/financial_forward_source.py',
            'laya_cell.current100k_forward': 'learn/laya_cell/current100k_forward.py',
            'laya_cell.stage_current100k_warm_flat': 'learn/laya_cell/stage_current100k_warm_flat.py',
            'laya_cell.resident_store': 'learn/laya_cell/resident_store.py'})
        modules = {}; inventory = {}
        for name, relative in mapping.items():
            path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# synthetic source ' + name + '\n')
            inventory[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            module = types.ModuleType(name); module.__file__ = str(path); modules[name] = module
        return {'worker_source_root': str(root), 'worker_runtime_sha256': inventory}, modules
    def test_every_import_matches_new_runtime_and_byte_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            c, modules = self.fixture(Path(directory).resolve())
            with patch.dict(sys.modules, modules): precheck.check_precheck_source(c)
    def test_legitimate_disk_copy_does_not_authorize_same_named_external_import(self):
        with tempfile.TemporaryDirectory() as directory:
            c, modules = self.fixture(Path(directory).resolve())
            modules['activate_prepared_driver'].__file__ = '/different/runtime/activate_prepared_driver.py'
            with patch.dict(sys.modules, modules), self.assertRaisesRegex(ValueError, 'IMPORTED_SOURCE_OUTSIDE_RUNTIME'):
                precheck.check_precheck_source(c)
    def test_changed_runtime_file_is_not_accepted_by_original_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            c, modules = self.fixture(Path(directory).resolve())
            Path(modules['precheck_current100k_registration'].__file__).write_text('changed')
            with patch.dict(sys.modules, modules), self.assertRaisesRegex(ValueError, 'SOURCE_PIN_CHANGED'):
                precheck.check_precheck_source(c)


class WakeCandidateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(); self.release = self.root / 'release'
        self.release.mkdir(); self.inventory = {}
        for name in precheck.SUPPORT_FILES:
            path = self.release / 'scripts' / name; path.parent.mkdir(exist_ok=True)
            data = ('# pinned test ' + name + '\n').encode()
            if name == 'activate_current100k_warm_stage.py':
                data += b"ACTIVATION_PROFILE = 'SAME_PLAN_FINITE_WARM_ACTIVATION_V1'\n"
            path.write_bytes(data)
            self.inventory['scripts/' + name] = hashlib.sha256(data).hexdigest()
        self.launcher = self.release / 'bin/current100k-warm-launch.py'; self.launcher.parent.mkdir()
        self.launcher.write_text('# fixture launcher\n')
        self.inventory['bin/current100k-warm-launch.py'] = self.sha(self.launcher)
        forward = self.release / 'learn/laya_cell/current100k_forward.py'; forward.parent.mkdir(parents=True)
        forward.write_text("REGISTRATION_PUBLISH_PROFILE = 'ATOMIC_SIGNED_PLAN_DIRECTORY_V1'\n")
        self.inventory['learn/laya_cell/current100k_forward.py'] = self.sha(forward)
        self.config = {'worker_source_root': str(self.release), 'worker_runtime_sha256': self.inventory}
        self.config_path = self.root / 'registrar-config.json'; self.config_path.write_bytes(raw(self.config))
        self.request = {'registration_not_before_ms': 1794557397469}
        self.request_path = self.root / 'request.json'; self.request_path.write_bytes(raw(self.request))
        self.command = ['/usr/bin/python3', '-B', str(self.launcher), '--config', str(self.config_path),
            '--sha256', self.sha(self.config_path), '--mode', 'register', '--request', str(self.request_path),
            '--request-sha256', self.sha(self.request_path)]
        self.stage = {'schema': 'heptatrader.current100k-warm-flat-stage.v1',
            'state': 'STAGED_CPU_VERIFIED_NOT_REGISTERED', 'trial_registered': False,
            'configs': {'registrar': {'path': str(self.config_path), 'sha256': self.sha(self.config_path)}},
            'registration_request': str(self.request_path), 'registration_request_sha256': self.sha(self.request_path),
            'release': str(self.release), 'prior_outcome_end_ms': self.request['registration_not_before_ms'],
            'explicit_argv': {'registrar': self.command},
            'pythonpath': ':'.join(str(self.release / part) for part in ('research', 'learn', 'scripts')),
            'registration_publish_profile': precheck.ATOMIC_REGISTRATION_PROFILE,
            'registration_activation_profile': 'SAME_PLAN_FINITE_WARM_ACTIVATION_V1'}
        self.stage_path = self.root / 'WARM_STAGE.json'
        self.deadline = self.request['registration_not_before_ms'] + 3600000
        self.selection_path = self.root / 'selection.json'
        self.selection_path.write_bytes(raw({'schema': 'heptatrader.current100k-shared-priority-selection.v1',
            'activation_deadline_ms': self.deadline, 'roles': {'trainer': {}, 'evaluator': {}}}))
        import activate_current100k_warm_stage as activation
        validation = patch.object(activation, 'stage_inputs')
        self.stage_validation = validation.start(); self.addCleanup(validation.stop)

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def prepare(self, mode='precheck', readmission_seconds=0):
        self.stage_path.write_bytes(raw(self.stage))
        return precheck.wake_candidates(self.stage_path, self.sha(self.stage_path), self.deadline, mode, readmission_seconds,
            self.selection_path if mode == 'register' else None, self.sha(self.selection_path) if mode == 'register' else None)

    def new_producer_scope(self):
        directory = self.root / 'new-candidate'; directory.mkdir()
        candidate_path = directory / 'candidate.json'
        candidate = {'schema': 'heptatrader.current100k-value-candidate.v1',
            'candidate_id': 'new-candidate-id', 'created_at_ms': 1793600000000}
        candidate_path.write_bytes(raw(candidate))
        self.request.update(registration_scope='NEW_PRODUCER_SCOPE',
            registration_not_before_ms=candidate['created_at_ms'], candidate_directory=str(directory),
            input_sha256={str(candidate_path): self.sha(candidate_path)})
        self.request_path.write_bytes(raw(self.request))
        self.stage.update(registration_scope='NEW_PRODUCER_SCOPE', candidate_id=candidate['candidate_id'],
            registration_request_sha256=self.sha(self.request_path))
        self.command[-1] = self.sha(self.request_path)
        return candidate_path

    def test_separate_one_time_utc_precheck_is_finite_and_read_only(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        result = self.prepare()
        self.assertEqual(result['calendar_utc'], '2026-11-13 08:09:58 UTC')
        self.assertIn('--registration-precheck', result['argv'])
        self.assertEqual(result['timer'].count('OnCalendar='), 1)
        for required in ('Persistent=true', 'RemainAfterElapse=yes', 'WakeSystem=false'):
            self.assertIn(required, result['timer'])
        for forbidden in ('OnBootSec=', 'OnUnitActiveSec=', 'OnUnitInactiveSec='):
            self.assertNotIn(forbidden, result['timer'])
        for required in ('Type=oneshot', 'Restart=no', 'TimeoutStartSec=180', 'KillMode=control-group'):
            self.assertIn(required, result['service'])
        self.assertNotIn('[Install]', result['service'])
        self.assertNotIn('heptatrader-current100k-forward-predictor', result['timer'])
        self.assertFalse(result['installed']); self.assertFalse(result['registration_performed'])
        self.assertFalse(result['physical_wakeup_guaranteed'])
        self.assertEqual(sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')),
                         sorted([*before, 'WARM_STAGE.json']))

    def test_original_fractional_deadline_is_never_rounded_early(self):
        result = self.prepare()
        seconds = datetime.strptime(result['calendar_utc'], '%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc).timestamp()
        self.assertGreaterEqual(int(seconds * 1000), self.request['registration_not_before_ms'])
        self.assertLess(int(seconds * 1000) - self.request['registration_not_before_ms'], 1000)

    def test_new_producer_can_wake_after_completed_fit_before_prior_window_ends(self):
        self.new_producer_scope()
        result = self.prepare()
        self.assertEqual(result['registration_scope'], 'NEW_PRODUCER_SCOPE')
        calendar = datetime.strptime(result['calendar_utc'], '%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc)
        self.assertEqual(int(calendar.timestamp()*1000), self.request['registration_not_before_ms'])
        self.assertLess(self.request['registration_not_before_ms'], self.stage['prior_outcome_end_ms'])
        self.assertEqual(self.stage['prior_outcome_end_ms'], 1794557397469)

    def test_new_producer_requires_pinned_completed_candidate_and_matching_scope(self):
        candidate_path = self.new_producer_scope()
        before = candidate_path.read_bytes(); candidate_path.write_bytes(before + b' ')
        with self.assertRaisesRegex(ValueError, 'SHA256_CHANGED'):
            self.prepare()
        candidate_path.write_bytes(before)
        self.stage['registration_scope'] = 'SAME_PRODUCER_REFIT'
        with self.assertRaisesRegex(ValueError, 'REGISTRATION_SCOPE_CHANGED'):
            self.prepare()

    def test_new_producer_time_cannot_substitute_earlier_unpinned_boundary(self):
        self.new_producer_scope()
        self.request['registration_not_before_ms'] -= 1
        self.request_path.write_bytes(raw(self.request))
        self.stage['registration_request_sha256'] = self.sha(self.request_path)
        self.command[-1] = self.sha(self.request_path)
        with self.assertRaisesRegex(ValueError, 'FINITE_DEADLINE_REQUIRED'):
            self.prepare()

    def test_register_wake_requires_complete_bound_activation_and_no_process_restart(self):
        result = self.prepare('register')
        self.assertNotIn('--registration-precheck', result['argv'])
        self.assertFalse(result['automatic_process_restart'])
        self.assertFalse(result['bounded_calendar_readmission'])
        self.assertIn('TimeoutStartSec=1500', result['service'])
        self.assertEqual(result['argv'][2], str(self.release / 'scripts/activate_current100k_warm_stage.py'))
        self.assertIn('--selection-request-sha256', result['argv'])
        self.stage_validation.assert_called_once()
        self.stage['registration_publish_profile'] = None
        with self.assertRaisesRegex(ValueError, 'ATOMIC_REGISTRATION_PROFILE_REQUIRED'):
            self.prepare('register')
        self.assertEqual(self.prepare()['mode'], 'precheck')

    def test_receipt_profile_cannot_substitute_for_pinned_atomic_register_source(self):
        forward = self.release / 'learn/laya_cell/current100k_forward.py'
        forward.write_text('# old non-atomic register\n')
        self.inventory['learn/laya_cell/current100k_forward.py'] = self.sha(forward)
        self.config_path.write_bytes(raw(self.config)); self.stage['configs']['registrar']['sha256'] = self.sha(self.config_path)
        self.command[6] = self.sha(self.config_path)
        with self.assertRaisesRegex(ValueError, 'ATOMIC_REGISTRATION_SOURCE_REQUIRED'):
            self.prepare('register')
    def test_old_stage_without_checker_cannot_be_silently_patched_or_scheduled(self):
        del self.inventory['scripts/precheck_current100k_registration.py']
        self.config_path.write_bytes(raw(self.config)); self.stage['configs']['registrar']['sha256'] = self.sha(self.config_path)
        self.command[6] = self.sha(self.config_path)
        with self.assertRaisesRegex(ValueError, 'CHECKER_NOT_STAGED'):
            self.prepare()

    def test_changed_helper_or_launcher_refuses(self):
        for path in (self.release / 'scripts/activate_prepared_driver.py', self.launcher):
            original = path.read_bytes(); path.write_bytes(original + b'changed')
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, 'CHECKER_NOT_STAGED|LAUNCHER_CHANGED'):
                self.prepare()
            path.write_bytes(original)

    def test_changed_argv_and_request_pin_refuse(self):
        self.command.append('--check-only')
        with self.assertRaisesRegex(ValueError, 'ARGV_CHANGED'):
            self.prepare()
        self.command.pop(); self.request_path.write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'SHA256_CHANGED'):
            self.prepare()

    def test_readmission_calendar_is_finite_and_stops_effective_work_after_success(self):
        result = self.prepare(readmission_seconds=60)
        self.assertEqual(result['calendar_events'], 60)
        self.assertTrue(result['bounded_calendar_readmission'])
        self.assertFalse(result['exactly_once_execution_guaranteed'])
        self.assertEqual(result['timer'].count('OnCalendar='), 60)
        self.assertIn('RemainAfterExit=yes', result['service'])
        self.assertIn('Restart=no', result['service'])
        last = datetime.strptime(result['calendars_utc'][-1], '%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc)
        self.assertLessEqual(int(last.timestamp()*1000), self.deadline)
        self.deadline += 24*3600000
        with self.assertRaisesRegex(ValueError, 'FINITE_CALENDAR_BOUND'):
            self.prepare(readmission_seconds=60)

    def test_finite_61_second_readmissions_do_not_lock_to_stale_120_second_producer_phase(self):
        self.deadline = self.request['registration_not_before_ms'] + (119*61+1)*1000
        result = self.prepare(readmission_seconds=61)
        seconds = [int(datetime.strptime(value, '%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc).timestamp())
                   for value in result['calendars_utc']]
        self.assertEqual(len(seconds), 120)
        self.assertEqual(len({value % 120 for value in seconds}), 120)
        # Every possible fixed source phase has actual wake opportunities in
        # the original strict freshness bound. The registrar still rechecks it.
        for source_phase in range(120):
            self.assertTrue(any(0 <= (value-source_phase) % 120 < 60 for value in seconds))
    def test_finite_wake_deadline_must_follow_earliest_time(self):
        self.deadline = self.request['registration_not_before_ms']
        with self.assertRaisesRegex(ValueError, 'FINITE_DEADLINE_REQUIRED'):
            self.prepare()

    def test_interrupted_pure_wake_publication_resumes_identical_pins(self):
        from laya_cell import immutable_files
        real = immutable_files.write_immutable; result = self.prepare(); output = self.root / 'wake'
        def interrupted(path, *args, **kwargs):
            if path.name == result['timer_name'] + '.candidate':
                raise RuntimeError('injected staging interruption')
            return real(path, *args, **kwargs)
        with patch.object(immutable_files, 'write_immutable', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'injected staging interruption'):
                precheck.publish_wake(output, result)
        service = output / (result['service_name'] + '.candidate'); before = service.read_bytes()
        self.assertFalse((output / 'WAKE_STAGE.json').exists())
        recovered = precheck.publish_wake(output, result, resume=True)
        self.assertEqual(service.read_bytes(), before)
        self.assertEqual(json.loads((output / 'WAKE_STAGE.json').read_bytes()), recovered)
        self.assertFalse(recovered['registration_performed']); self.assertFalse(recovered['installed'])

    def test_wake_resume_cannot_change_deadline_or_existing_unit_bytes(self):
        from laya_cell.integrity import ContractError
        result = self.prepare(); output = self.root / 'wake'; precheck.publish_wake(output, result)
        with self.assertRaisesRegex(ValueError, 'STAGING_REQUEST_CHANGED'):
            precheck.publish_wake(output, {**result, 'deadline_ms': result['deadline_ms']+1}, resume=True)
        service = output / (result['service_name'] + '.candidate')
        service.chmod(0o600); service.write_text('changed unit'); service.chmod(0o400)
        with self.assertRaisesRegex(ContractError, 'IMMUTABLE_WAKE_STAGING_BYTES_CHANGED'):
            precheck.publish_wake(output, result, resume=True)
        self.assertEqual(service.read_text(), 'changed unit')


if __name__ == '__main__':
    unittest.main()
