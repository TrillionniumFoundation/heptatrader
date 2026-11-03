"""Future profile selection, complete hardware receipts, and pre-fit handoff."""
import copy
import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import continue_current100k_new_scope as continuation
from tests.python import test_current100k_new_scope_continuation as previous_tests


class HardwareEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.source = self.root / 'new-executor'
        self.tokenizer = self.source / 'research/neuron_learning/laya_cell/tokenizer.py'
        self.tokenizer.parent.mkdir(parents=True); self.tokenizer.write_text('future tokenizer implementation\n')
        self.prior = {'plan_id': 'prior-plan', 'candidate_id': 'prior-candidate',
            'base_id': 'prior-base', 'head_tensor_sha256': 'a'*64}
        self.report = {'state': 'HISTORICAL_PARITY_EXACT',
            'scope': 'HISTORICAL_CPU_STANDBY_PARITY_NOT_REGISTERED_FORWARD_PROFILE',
            'exact_parity': True, 'historical_loader_exact_parity': True,
            'plan_id': self.prior['plan_id'], 'plan_sha256': 'b'*64,
            'candidate_id': self.prior['candidate_id'], 'head_sha256': 'a'*64, 'base_id': self.prior['base_id'],
            'cpu_proof': {'all_parameters_and_buffers_cpu': True, 'cuda_initialized': False, 'gpu_device_fds': []},
            'initial_parameters_and_all_buffers': {'exact': True},
            'after_inference_parameters_and_all_buffers': {'exact': True},
            'tokenizer_input_ids_and_masks_exact': True, 'features_bitwise_equal': True, 'values_bitwise_equal': True,
            'feature_max_absolute_difference': 0.0, 'value_max_absolute_difference_usd': 0.0,
            'forward_seal_created': False, 'v2_plan_registered': False, 'optimizer_used': False,
            'publish_path_exercised': False, 'broker_orders_sent': 0,
            'future_tokenizer_parity': {'requested': True, 'exact': True,
                'private_module_only_no_laya_module_replaced': True, 'same_original_gpu_model_and_head': True,
                'tokenizer_input_ids_and_masks_exact': True, 'features_bitwise_equal': True, 'values_bitwise_equal': True,
                'feature_max_absolute_difference': 0.0, 'value_max_absolute_difference_usd': 0.0,
                'source': {'path': '/diagnostic/same-bytes/tokenizer.py', 'sha256': continuation.sha(self.tokenizer)}}}
        self.lifecycle = {'scope': self.report['scope'], 'phase': 'COMPLETE', 'error': None,
            'child_reaped': True, 'initial_lease_released': True, 'fresh_admission_granted': True,
            'fresh_lease_released_after_reap': True, 'parent_imported_torch': False,
            'completion_receipt': {'exact_parity': True}, 'forward_seal_created': False, 'broker_orders_sent': 0}
        pins = {name: 'd'*64 for name in ('staged-result.json', 'staged-state-hashes.json',
            'allocation-after-destroy.json', 'future-tokenizer-result.json')}
        self.report.update(comparison_mode='separate-process', model_processes_never_overlap=True,
            first_model_memory_released_by='PROCESS_EXIT_AND_REAP',
            first_model_gpu_allocated_bytes_after_destroy=32*1024**2,
            staged_child_exit_code=0, direct_child_exit_code=0, staged_result_files_sha256=pins)
        self.lifecycle.update(comparison_mode='separate-process', staged_child_reaped=True,
            staged_gpu_lease_released_after_reap=True, direct_original_admission_granted=True,
            direct_child_started=True, direct_child_reaped=True, direct_lease_released_after_reap=True,
            staged_child_exit_code=0, direct_child_exit_code=0, staged_completion_receipt={'files_sha256': pins})
        self.lifecycle['completion_receipt']['kind'] = 'PARENT_TWO_PROCESS_COMPARISON'
        self.request = {'worker_profile': 'cpu-standby-v2', 'source_repository': str(self.source)}
        self.seal()

    def seal(self, *, bind_report=True):
        report = self.root / 'report.json'; lifecycle = self.root / 'lifecycle.json'
        report.write_bytes(continuation.canonical(self.report)+b'\n')
        if bind_report:
            self.lifecycle['completion_receipt']['report_sha256'] = continuation.sha(report)
        lifecycle.write_bytes(continuation.canonical(self.lifecycle)+b'\n')
        self.request['worker_profile_evidence'] = {
            name: {'path': str(path), 'sha256': continuation.sha(path)}
            for name, path in (('report', report), ('lifecycle', lifecycle))}

    def verify(self):
        return continuation.hardware_evidence(self.request, self.prior, 'b'*64)

    def test_complete_two_receipt_proof_accepts_identical_relocated_tokenizer(self):
        verified = self.verify()
        self.assertEqual(verified, {'report': self.report, 'lifecycle': self.lifecycle})
        self.assertFalse(verified['report']['v2_plan_registered'])
        self.assertFalse(verified['report']['forward_seal_created'])

    def test_absent_profile_preserves_v1_and_v1_cannot_claim_v2_hardware(self):
        self.assertEqual(continuation.worker_profile({}), 'warm-flat-v1')
        self.assertIsNone(continuation.hardware_evidence({}, {}, 'unused'))
        with self.assertRaisesRegex(ValueError, 'V1_HAS_NO_STANDBY_HARDWARE_SELECTION'):
            continuation.hardware_evidence({**self.request, 'worker_profile': 'warm-flat-v1'}, {}, 'unused')
        with self.assertRaisesRegex(ValueError, 'WORKER_PROFILE_REQUIRED'):
            continuation.worker_profile({'worker_profile': 'unsealed-profile'})

    def test_report_alone_and_staged_only_parity_are_insufficient(self):
        pin = copy.deepcopy(self.request['worker_profile_evidence'])
        for incomplete in (None, pin['report'], {'report': pin['report']}, {'lifecycle': pin['lifecycle']}):
            with self.subTest(pin=incomplete), self.assertRaisesRegex(ValueError, 'HARDWARE_PROOF_REQUIRED'):
                continuation.hardware_evidence({**self.request, 'worker_profile_evidence': incomplete}, self.prior, 'b'*64)
        self.report['state'] = 'STAGED_INFERENCE_COMPLETE'; self.seal()
        with self.assertRaisesRegex(ValueError, 'HARDWARE_PROOF_OR_PRIOR_CHANGED'): self.verify()

    def test_historical_candidate_base_head_and_plan_cannot_be_substituted(self):
        for field in ('plan_id', 'plan_sha256', 'candidate_id', 'head_sha256', 'base_id', 'historical_loader_exact_parity'):
            before = self.report[field]; self.report[field] = 'different'; self.seal()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'HARDWARE_PROOF_OR_PRIOR_CHANGED'):
                self.verify()
            self.report[field] = before

    def test_lifecycle_must_prove_reap_fresh_admission_release_and_parent_isolation(self):
        bad = {'phase': 'GPU_PARITY', 'error': 'timeout after child report', 'child_reaped': False,
            'initial_lease_released': False, 'fresh_admission_granted': False,
            'fresh_lease_released_after_reap': False, 'parent_imported_torch': True,
            'forward_seal_created': True, 'broker_orders_sent': 1}
        for field, value in bad.items():
            before = self.lifecycle[field]; self.lifecycle[field] = value; self.seal()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'HARDWARE_LIFECYCLE_NOT_COMPLETE'):
                self.verify()
            self.lifecycle[field] = before
        del self.lifecycle['error']; self.seal()
        with self.assertRaisesRegex(ValueError, 'HARDWARE_LIFECYCLE_NOT_COMPLETE'): self.verify()

    def test_lifecycle_receipt_must_name_this_exact_report(self):
        self.lifecycle['completion_receipt']['report_sha256'] = 'c'*64; self.seal(bind_report=False)
        with self.assertRaisesRegex(ValueError, 'HARDWARE_LIFECYCLE_NOT_COMPLETE'): self.verify()
        self.lifecycle['completion_receipt']['exact_parity'] = False; self.seal()
        with self.assertRaisesRegex(ValueError, 'HARDWARE_LIFECYCLE_NOT_COMPLETE'): self.verify()

    def test_both_files_are_repinned_on_every_validation(self):
        for name in ('report', 'lifecycle'):
            self.seal(); path = Path(self.request['worker_profile_evidence'][name]['path'])
            path.write_bytes(path.read_bytes()+b' ')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'INPUT_CHANGED'): self.verify()

    def test_future_tokenizer_comparison_is_required_for_new_executor(self):
        future = self.report['future_tokenizer_parity']
        for field in ('requested', 'exact', 'tokenizer_input_ids_and_masks_exact', 'features_bitwise_equal',
                      'values_bitwise_equal', 'private_module_only_no_laya_module_replaced', 'same_original_gpu_model_and_head'):
            future[field] = False; self.seal()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'FUTURE_TOKENIZER_PARITY_OR_SOURCE_CHANGED'):
                self.verify()
            future[field] = True
        future['feature_max_absolute_difference'] = 1e-12; self.seal()
        with self.assertRaisesRegex(ValueError, 'FUTURE_TOKENIZER_PARITY_OR_SOURCE_CHANGED'): self.verify()
        future['feature_max_absolute_difference'] = 0; self.seal()
        self.tokenizer.write_text('a later executor implementation\n')
        with self.assertRaisesRegex(ValueError, 'FUTURE_TOKENIZER_PARITY_OR_SOURCE_CHANGED'): self.verify()

    def test_cpu_or_parameter_or_output_parity_failure_cannot_be_waived(self):
        self.report['cpu_proof']['gpu_device_fds'] = ['/dev/kfd']; self.seal()
        with self.assertRaisesRegex(ValueError, 'HARDWARE_NOT_CPU_ONLY'): self.verify()
        self.report['cpu_proof']['gpu_device_fds'] = []
        self.report['after_inference_parameters_and_all_buffers']['exact'] = False; self.seal()
        with self.assertRaisesRegex(ValueError, 'HARDWARE_PARITY_NOT_EXACT'): self.verify()
        self.report['after_inference_parameters_and_all_buffers']['exact'] = True
        self.report['optimizer_used'] = True; self.seal()
        with self.assertRaisesRegex(ValueError, 'HARDWARE_DIAGNOSTIC_SCOPE_CHANGED'): self.verify()

    def test_two_child_baseline_requires_both_reaps_and_original_admissions(self):
        # The completed first child must exit normally. Its process-owned
        # 32 MiB workspace need not reach zero before process exit.
        self.verify()
        for field in ('staged_child_reaped', 'staged_gpu_lease_released_after_reap', 'direct_original_admission_granted',
                      'direct_child_started', 'direct_child_reaped', 'direct_lease_released_after_reap'):
            self.lifecycle[field] = False; self.seal()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'TWO_CHILD_LIFECYCLE_NOT_COMPLETE'):
                self.verify()
            self.lifecycle[field] = True
        self.lifecycle['direct_child_exit_code'] = self.report['direct_child_exit_code'] = 1; self.seal()
        with self.assertRaisesRegex(ValueError, 'TWO_CHILD_LIFECYCLE_NOT_COMPLETE'): self.verify()
        self.lifecycle['direct_child_exit_code'] = self.report['direct_child_exit_code'] = 0
        self.report['first_model_memory_released_by'] = 'EMPTY_CACHE_ONLY'; self.seal()
        with self.assertRaisesRegex(ValueError, 'TWO_CHILD_LIFECYCLE_NOT_COMPLETE'): self.verify()
        self.report['first_model_memory_released_by'] = 'PROCESS_EXIT_AND_REAP'
        self.lifecycle['comparison_mode'] = 'same-process'; self.seal()
        with self.assertRaisesRegex(ValueError, 'TWO_CHILD_LIFECYCLE_NOT_COMPLETE'): self.verify()

    def test_both_completed_children_require_integer_zero_exit_status(self):
        for field in ('staged_child_exit_code', 'direct_child_exit_code'):
            for value in (-15, -9, 1, False, 0.0, None):
                self.lifecycle[field] = self.report[field] = value; self.seal()
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'TWO_CHILD_LIFECYCLE_NOT_COMPLETE'):
                    self.verify()
            self.lifecycle[field] = self.report[field] = 0


class ProfileRouteTests(unittest.TestCase):
    sha = staticmethod(previous_tests.PreparationSourceTests.sha)
    write = previous_tests.PreparationSourceTests.write

    def setUp(self):
        previous_tests.PreparationSourceTests.setUp(self)
        self.request['worker_profile'] = 'cpu-standby-v2'
        Path(self.request['controller_root']).mkdir()
        self.fit_path = self.root / 'FIT_STAGE.json'; self.write(self.fit_path, {'fixed': 'fit'})
        self.readiness = {'dataset_sha256': 'd'*64, 'expected_trial': 2}
        self.candidate = {'candidate_id': 'new-candidate'}
        self.stage = {'worker_profile': 'cpu-standby-v2', 'candidate_id': 'new-candidate',
            **self.readiness, 'registration_scope': 'NEW_PRODUCER_SCOPE',
            'forward_root': self.request['forward_root'], 'source_commit': self.request['source_commit'],
            'fit_stage_sha256': self.sha(self.fit_path)}

    def test_ordinary_newscope_stager_receives_explicit_profile_and_same_history(self):
        path = Path(self.request['controller_root']) / 'warm-stage/WARM_STAGE.json'
        def stage_child(argv, *args, **kwargs):
            self.assertEqual(argv[argv.index('--worker-profile')+1], 'cpu-standby-v2')
            self.assertEqual(argv[argv.index('--registration-scope')+1], 'NEW_PRODUCER_SCOPE')
            self.assertEqual(argv[argv.index('--legacy-plan')+1], str(self.history_path))
            self.assertIn('--resume-staging', argv)
            self.write(path, self.stage)
            return subprocess.CompletedProcess(argv, 0, '', '')
        with patch.object(continuation, 'child', side_effect=stage_child) as child:
            result = continuation.warm_stage(self.request, {'source': self.repo, 'prep': self.prep},
                self.fit_path, self.candidate, self.readiness)
        child.assert_called_once(); self.assertEqual(result, (path, self.stage))

    def test_partial_stage_cannot_silently_choose_legacy_profile(self):
        path = Path(self.request['controller_root']) / 'warm-stage/WARM_STAGE.json'
        self.write(path, {key: value for key, value in self.stage.items() if key != 'worker_profile'})
        with patch.object(continuation, 'child') as child, \
                self.assertRaisesRegex(ValueError, 'WARM_STAGE_WORKER_PROFILE_CHANGED'):
            continuation.warm_stage(self.request, {'source': self.repo, 'prep': self.prep},
                self.fit_path, self.candidate, self.readiness)
        child.assert_not_called()
        self.assertFalse((Path(self.request['controller_root']) / 'WARM_STAGE_BOUND.json').exists())


class PreFitTimerHandoffTests(unittest.TestCase):
    sha = staticmethod(previous_tests.PreparationSourceTests.sha)
    write = previous_tests.PreparationSourceTests.write

    def setUp(self):
        previous_tests.PreparationSourceTests.setUp(self)
        patch.object(continuation, 'repository_check').start()
        self.old = {**self.request, 'source_repository': str(self.root / 'old-executor'), 'source_commit': 'old-commit',
            'controller_root': str(self.root / 'old-controller'), 'fit_output_root': str(self.root / 'old-new-fit'),
            'forward_root': str(self.root / 'old-new-forward')}
        self.old_path = Path(self.old['controller_root']) / 'CONTINUATION_REQUEST.json'; self.write(self.old_path, self.old)
        self.old_sha = self.sha(self.old_path)
        self.request.update(worker_profile='cpu-standby-v2',
            supersedes_continuation={'request': str(self.old_path), 'sha256': self.old_sha})
        self.path = Path(self.request['controller_root']) / 'CONTINUATION_REQUEST.json'; self.write(self.path, self.request)
        self.request_sha = self.sha(self.path)
        self.previous = continuation.predecessor_inputs(self.request)
        self.inputs = {'predecessor': self.previous, 'prep_path': self.prep_path}
        self.cohort_path = self.prep_path.parent / 'FROZEN_NEW_PRODUCER_32.json'
        self.write(self.cohort_path, {'existing_cohort': ['original-row-'+str(i) for i in range(32)]})
        self.before = {path: path.read_bytes() for path in (self.old_path, self.prep_path, self.cohort_path)}
        self.home = self.root / 'home'; self.units_dir = self.home / '.config/systemd/user'
        self.units_dir.mkdir(parents=True); patch.object(Path, 'home', return_value=self.home).start()
        self.old_units = continuation.service_units(self.old_path, self.old_sha, self.old)
        self.new_units = continuation.service_units(self.path, self.request_sha, self.request)
        self.properties = {}; self.publish_units(self.old_units)
        self.old_timer = next(name for name in self.old_units if name.endswith('.timer'))
        patch.object(continuation, 'native_continuation_unit', side_effect=lambda name: copy.deepcopy(self.properties[name])).start()
        self.control = patch.object(continuation.subprocess, 'run', side_effect=self.disable_only_old_timer).start()
        self.install = patch.object(continuation, 'install_units', side_effect=self.publish_units).start()

    def publish_units(self, units):
        for name, text in units.items():
            path = self.units_dir / name
            if path.exists(): self.assertEqual(path.read_text(), text)
            else: path.write_text(text); path.chmod(0o600)
            service = name.endswith('.service')
            props = {'LoadState': 'loaded', 'FragmentPath': str(path), 'DropInPaths': '',
                'NeedDaemonReload': 'no', 'ActiveState': 'inactive' if service else 'active',
                'SubState': 'dead' if service else 'waiting', 'MainPID': '0',
                'UnitFileState': 'static' if service else 'enabled', 'NextElapseUSecRealtime': 'next-calendar-slot'}
            if service:
                values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
                props.update(ExecStart='argv[]='+values['ExecStart']+' ;', WorkingDirectory=values['WorkingDirectory'],
                    Type=values['Type'], Environment=' '.join(line.removeprefix('Environment=')
                        for line in text.splitlines() if line.startswith('Environment=')))
            self.properties[name] = props

    def disable_only_old_timer(self, argv, **kwargs):
        self.assertEqual(argv, ['systemctl', '--user', 'disable', '--now', self.old_timer])
        self.properties[self.old_timer].update(ActiveState='inactive', SubState='dead', UnitFileState='disabled')
        return subprocess.CompletedProcess(argv, 0, '', '')

    def run_install(self):
        return continuation.install_continuation(self.path, self.request_sha, self.request, self.inputs)

    def assert_preserved(self):
        for path, raw in self.before.items(): self.assertEqual(path.read_bytes(), raw)

    def test_handoff_retires_only_exact_old_timer_and_preserves_preparation_and_cohort(self):
        proof = self.run_install(); self.assert_preserved()
        self.assertEqual(proof['state'], 'PREDECESSOR_TIMER_RETIRED_NEW_TIMER_VERIFIED')
        self.assertFalse(proof['current100k_p0_closed']); self.assertFalse(proof['training_started_by_installer'])
        self.assertEqual(proof['preserved_cohort_sha256'][str(self.cohort_path)], self.sha(self.cohort_path))
        self.control.assert_called_once(); self.install.assert_called_once_with(self.new_units)
        with continuation.predecessor_lock(self.request, self.inputs, require_installed=True):
            fd = os.open(self.previous['root'] / 'continuation.lock', os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError): fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally: os.close(fd)

    def test_old_busy_or_preparation_busy_causes_no_unit_mutation(self):
        for path, reason in ((self.previous['root'] / 'continuation.lock', 'PREDECESSOR_BUSY_NO_FORCE_STOP'),
                             (self.prep_path.parent / 'preparation.lock', 'PREPARATION_BUSY_NO_TIMER_HANDOFF')):
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.subTest(lock=path.name), self.assertRaisesRegex(ValueError, reason): self.run_install()
            finally: os.close(fd)
        self.control.assert_not_called(); self.install.assert_not_called(); self.assert_preserved()

    def test_old_staging_unknown_effects_or_output_prevent_handoff(self):
        effects = (self.previous['root'] / 'fit-stage', Path(self.old['fit_output_root']), Path(self.old['forward_root']))
        for output in effects:
            output.mkdir()
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'PREDECESSOR_HAS_'):
                self.run_install()
            output.rmdir()
        self.control.assert_not_called(); self.install.assert_not_called()

    def test_contract_or_output_reuse_cannot_be_hidden_by_new_source(self):
        for field in ('preparation_request_sha256', 'preparation_source_commit', 'guard_config_sha256',
                      'activation_readmission_seconds', 'fit_timeout_seconds'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'PREDECESSOR_CONTRACT_CHANGED'):
                continuation.predecessor_inputs({**self.request, field: 'changed'})
        for field in ('controller_root', 'source_repository', 'fit_output_root', 'forward_root'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'PREDECESSOR_OUTPUTS_MUST_REMAIN_SEPARATE'):
                continuation.predecessor_inputs({**self.request, field: self.old[field]})

    def test_loaded_old_executor_or_disk_unit_change_blocks_before_retirement(self):
        service = next(name for name in self.old_units if name.endswith('.service'))
        for field, changed in (('WorkingDirectory', '/other/source'), ('Environment', 'PYTHONPATH=/other'),
                               ('ExecStart', 'argv[]=/other/program ;'), ('DropInPaths', '/other/dropin')):
            original = self.properties[service][field]; self.properties[service][field] = changed
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'CONTINUATION_LOADED_'): self.run_install()
            self.properties[service][field] = original
        (self.units_dir / service).write_text('different service bytes')
        with self.assertRaisesRegex(ValueError, 'CONTINUATION_UNIT_BYTES_CHANGED'): self.run_install()
        self.control.assert_not_called(); self.install.assert_not_called()

    def test_interrupted_install_resumes_same_request_without_replaying_training(self):
        self.install.side_effect = RuntimeError('interruption after old timer was retired')
        with self.assertRaisesRegex(RuntimeError, 'interruption'): self.run_install()
        marker = Path(self.request['controller_root']) / 'PREDECESSOR_HANDOFF.json'
        self.assertFalse(marker.exists()); self.assert_preserved()
        self.assertTrue((marker.parent / 'PREDECESSOR_HANDOFF_INTENT.json').exists())
        with self.assertRaisesRegex(ValueError, 'PREDECESSOR_HANDOFF_NOT_COMPLETE'):
            with continuation.predecessor_lock(self.request, self.inputs, require_installed=True): pass
        self.install.side_effect = self.publish_units
        self.run_install(); before = marker.read_bytes(); self.run_install()
        self.assertEqual(marker.read_bytes(), before); self.assert_preserved()
        self.assertFalse(Path(self.request['fit_output_root']).exists())

    def test_new_timer_must_be_enabled_waiting_with_next_event_before_receipt(self):
        def broken_install(units):
            self.publish_units(units)
            for name in units:
                if name.endswith('.timer'): self.properties[name]['NextElapseUSecRealtime'] = 'infinity'
        self.install.side_effect = broken_install
        with self.assertRaisesRegex(ValueError, 'CONTINUATION_TIMER_NOT_WAITING'): self.run_install()
        self.assertFalse((Path(self.request['controller_root']) / 'PREDECESSOR_HANDOFF.json').exists())
        self.assert_preserved()

    def test_runtime_rejects_reenabled_predecessor_or_changed_frozen_cohort(self):
        self.run_install()
        self.properties[self.old_timer]['UnitFileState'] = 'enabled'
        with self.assertRaisesRegex(ValueError, 'PREDECESSOR_TIMER_NOT_RETIRED'):
            with continuation.predecessor_lock(self.request, self.inputs, require_installed=True): pass
        self.properties[self.old_timer]['UnitFileState'] = 'disabled'
        self.cohort_path.write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError, 'PREDECESSOR_FROZEN_COHORT_CHANGED'):
            with continuation.predecessor_lock(self.request, self.inputs, require_installed=True): pass


class NativeInspectorScopeTests(unittest.TestCase):
    def test_only_request_digest_continuation_names_are_inspected(self):
        with patch.object(continuation.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0,
                'LoadState=loaded\nActiveState=active\n', '')) as run:
            name = 'heptatrader-current100k-newscope-'+'a'*12+'.timer'
            self.assertEqual(continuation.native_continuation_unit(name)['LoadState'], 'loaded')
            self.assertEqual(run.call_args.args[0][:4], ['systemctl', '--user', 'show', name])
            run.reset_mock()
            for invalid in ('heptatrader-paper-driver.service', 'heptatrader-current100k-warm-trial5-predictor.service',
                            'heptatrader-current100k-newscope-unpinned.timer'):
                with self.subTest(name=invalid), self.assertRaisesRegex(ValueError, 'CONTINUATION_UNIT_NAME_SCOPE'):
                    continuation.native_continuation_unit(invalid)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
