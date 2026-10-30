"""CPU integration fixtures for guarded financial HIP selection and publication."""
import builtins
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import weakref
from unittest.mock import Mock, patch

from historical_training import current100k_value_trial as trial
from historical_training import resident_resource_guard as guard
from laya_cell import current100k_forward_worker as worker
from laya_cell import financial_forward_predict as prediction
from laya_cell import resident_hip_runtime as hip
from laya_cell.integrity import ContractError, digest


HIP_SHA = '1' * 64
SELECTION = {'profile': hip.PROFILE, 'manifest': hip.MANIFEST, 'sha256': HIP_SHA}
T = 1700000000000


def proof(target='predict'):
    return {'schema': 'heptatrader.resident-hip-runtime-proof.v1', 'phase': 'BEFORE_PUBLISH',
            'target': target, 'manifest_sha256': HIP_SHA, 'candidate_sha256': '3' * 64,
            'pid': 1234, 'worker_admission_granted': False, 'extra_gpu_probe_performed': False,
            'queue_flags': {name: None for name in hip.QUEUE_FLAGS},
            'libraries': {name: {'loaded': True, 'sha256': '3' * 64, 'device_major': 8,
                                'device_minor': 1, 'inode': i + 1}
                          for i, name in enumerate(hip.LIBRARIES)}}


class FinancialHipTests(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.trace = []
        self.original = {'hip_runtime': copy.deepcopy(SELECTION), 'python': '/accepted/bin/python',
                         'runtime_directory': str(self.root / 'owner-runtime')}
        self.context = {'readonly': [str(self.root / 'accepted-overlay')]}
        self.enter(patch.object(hip, '_ACTIVE', None))

    def enter(self, context):
        return self.stack.enter_context(context)

    def trace_call(self, event, result=None):
        def call(*args, **kwargs):
            self.trace.append(event)
            return result
        return call

    def install_gate(self, admitted=True):
        self.gate = SimpleNamespace(acquire=Mock(side_effect=self.trace_call('gate', admitted)),
                                    release=Mock(side_effect=self.trace_call('release')),
                                    lease=SimpleNamespace(release=Mock(side_effect=self.trace_call('release'))))
        self.gate_type = self.enter(patch.object(guard, 'Gate', return_value=self.gate))
        self.enter(patch.object(guard, 'verify_config', return_value=self.original))
        self.enter(patch.object(hip, 'prepare_worker', side_effect=self.trace_call('prepare', self.context)))
        self.sandbox = self.enter(patch('sandbox.enter', side_effect=self.trace_call('sandbox', {})))
        self.enter(patch.object(hip, 'activate', side_effect=self.trace_call('activate')))
        importer = builtins.__import__
        def imports(name, *args, **kwargs):
            if name == 'torch':
                self.trace.append('torch')
                return SimpleNamespace()
            if name in ('laya_cell.model', 'laya_cell.financial_value_train'):
                self.fail('Model import preceded accepted actual HIP proof')
            return importer(name, *args, **kwargs)
        self.enter(patch('builtins.__import__', side_effect=imports))
        def refused(*args):
            self.trace.append('prove')
            raise ContractError('FIXTURE_UNVERIFIED_ACTUAL_HIP')
        self.prove = self.enter(patch.object(hip, 'prove', side_effect=refused))

    def trial_config(self):
        c = {'hip_runtime': copy.deepcopy(SELECTION), 'python': self.original['python'],
             'consumer_config': 'consumer.json', 'dataset_path': 'dataset.json',
             'dataset_sha256': 'd' * 64, 'guard_config': 'guard.json',
             'output_root': str(self.root / 'fit'), 'base_directory': str(self.root / 'base'),
             'source_root': str(self.root / 'source'), 'native_binary': str(self.root / 'native')}
        consumer = {'period_path': str(self.root / 'period'), 'runtime_root': str(self.root / 'consumer'),
                    'registration': {'manifest': str(self.root / 'producer')},
                    'reward_config': {'oms_path': str(self.root / 'oms')}}
        self.enter(patch.object(trial, 'validate_trial'))
        self.enter(patch.object(trial, 'read', side_effect=lambda name: {
            'consumer.json': consumer, 'dataset.json': {'rows': []}, 'guard.json': {}}[str(name)]))
        self.enter(patch.object(trial, 'validate_config', return_value={'journal_directory': str(self.root / 'journal')}))
        self.enter(patch('historical_training.export_current100k.export', return_value={'dataset_path': 'dataset.json'}))
        self.enter(patch.object(trial, 'selected_rows', return_value=[]))
        return c

    def worker_config(self):
        plan = {'schema': 'heptatrader.current100k-value-forward-plan.v1', 'plan_id': 'p',
                'input_start_ms': T - 1000, 'input_end_ms': T + 10000,
                'worker_runtime_inventory_digest': 'inventory', 'producer_registration': {},
                'training_dataset_path': str(self.root / 'dataset'), 'hip_runtime_manifest_sha256': HIP_SHA}
        folder = self.root / 'forward/plans/p'
        folder.mkdir(parents=True)
        (folder / 'plan.json').write_text(json.dumps(plan))
        guard_path = self.root / 'guard.json'
        guard_path.write_text('{}')
        c = {'hip_runtime': copy.deepcopy(SELECTION), 'python': self.original['python'],
             'forward_root': str(self.root / 'forward'), 'guard_config': str(guard_path),
             'period_sha256': 'period', 'period_path': str(self.root / 'period'),
             'worker_runtime_inventory_digest': 'inventory', 'oms_path': str(self.root / 'oms'),
             'attribution_directory': str(self.root / 'journal'), 'worker_read_only_paths': [],
             'worker_source_root': str(self.root / 'source'), 'role_key_path': str(self.root / 'key'),
             'witness_trust_path': str(self.root / 'trust')}
        self.enter(patch.object(worker, 'validate_runtime'))
        self.enter(patch.object(worker.time, 'time', return_value=T / 1000))
        self.enter(patch('laya_cell.financial_forward_witness.verify_trust'))
        self.enter(patch.object(prediction, 'RegisteredPlanSnapshot',
                         return_value=SimpleNamespace(full_validations=1, reuses=0)))
        snapshot = SimpleNamespace(full_scans=1, reuses=0, read=lambda *args: ({}, {
            'request': {'request': {'started_at_ms': T - 100, 'position_at_request': 0}}}, {}, []))
        self.enter(patch('laya_cell.financial_forward_source.VerifiedIngressSnapshot', return_value=snapshot))
        return c

    def test_trial_proves_actual_hip_after_gate_and_sandbox_before_model(self):
        c = self.trial_config()
        self.install_gate()
        with self.assertRaisesRegex(ContractError, 'FIXTURE_UNVERIFIED_ACTUAL_HIP'):
            trial.run(c)
        self.assertEqual(self.trace, ['gate', 'prepare', 'sandbox', 'activate', 'torch', 'prove'])
        self.gate.release.assert_not_called()
        self.assertIn(self.context['readonly'][0], self.sandbox.call_args.args[0]['read_only_paths'])
        self.prove.assert_called_once_with(self.context, 'AFTER_TORCH_BEFORE_MODEL')
        self.assertFalse((Path(c['output_root']) / 'candidate.json').exists())

    def test_trial_denied_gate_never_loads_gpu_or_creates_output(self):
        c = self.trial_config()
        self.install_gate(False)
        with self.assertRaisesRegex(ValueError, 'RESIDENT_ADMISSION_DENIED_NO_MODEL_LOADED'):
            trial.run(c)
        self.assertEqual(self.trace, ['gate'])
        self.assertFalse(Path(c['output_root']).exists())

    def test_trial_denied_before_output_can_retry_same_config_only_until_output_exists(self):
        c = self.trial_config()
        frozen = copy.deepcopy(c)
        self.install_gate(False)
        with self.assertRaisesRegex(ValueError, 'RESIDENT_ADMISSION_DENIED_NO_MODEL_LOADED'):
            trial.run(c)
        self.assertFalse(Path(c['output_root']).exists())
        self.assertEqual(c, frozen)
        self.gate.acquire.side_effect = self.trace_call('gate', True)
        with self.assertRaisesRegex(ContractError, 'FIXTURE_UNVERIFIED_ACTUAL_HIP'):
            trial.run(c)
        plan = Path(c['output_root']) / 'plan.json'
        self.assertTrue(plan.exists())
        before = plan.read_bytes()
        self.gate.acquire.reset_mock()
        with self.assertRaisesRegex(ValueError, 'TRIAL_OUTPUT_ALREADY_EXISTS_NO_REPLAY'):
            trial.run(c)
        self.gate.acquire.assert_not_called()
        self.assertEqual(plan.read_bytes(), before)

    def test_trial_cannot_drop_runtime_already_selected_by_guard(self):
        c = self.trial_config()
        c.pop('hip_runtime')
        self.install_gate()
        with self.assertRaisesRegex(ContractError, 'GUARD_SELECTION_CHANGED_OR_DROPPED'):
            trial.run(c)
        self.gate_type.assert_not_called()
        self.assertEqual(self.trace, [])

    def test_original_guard_source_paths_cannot_replace_pinned_trial_imports(self):
        c = self.trial_config()
        self.install_gate()
        original_paths = list(sys.path)
        def acquired(*args):
            sys.path.insert(0, '/guard-original-runtime/learn')
            self.trace.append('gate')
            return True
        self.gate.acquire.side_effect = acquired
        def activated(context):
            self.assertEqual(sys.path, original_paths)
            self.trace.append('activate')
        self.enter(patch.object(hip, 'activate', side_effect=activated))
        with self.assertRaisesRegex(ContractError, 'FIXTURE_UNVERIFIED_ACTUAL_HIP'):
            trial.run(c)
        self.assertEqual(sys.path, original_paths)

    def cleanup_trial(self, *, synchronize_error=None):
        c = self.trial_config()
        self.install_gate()
        self.original_lease = self.install_real_lease()
        self.prove.side_effect = self.trace_call('prove', proof('train'))
        cuda = SimpleNamespace(is_initialized=Mock(return_value=True),
                               synchronize=Mock(side_effect=synchronize_error or self.trace_call('synchronize')),
                               empty_cache=Mock(side_effect=self.trace_call('empty_cache')))
        self.enter(patch.object(trial.gc, 'collect', side_effect=self.trace_call('collect')))
        importer = builtins.__import__
        def imports(name, *args, **kwargs):
            if name == 'torch':
                self.trace.append('torch')
                return SimpleNamespace(cuda=cuda)
            if name == 'laya_cell.model':
                raise RuntimeError('FIXTURE_MODEL_IMPORT_FAILURE')
            return importer(name, *args, **kwargs)
        self.enter(patch('builtins.__import__', side_effect=imports))
        return c, cuda

    def test_trial_keeps_lease_until_proven_runtime_cleanup_finishes(self):
        c, cuda = self.cleanup_trial()
        with self.assertRaisesRegex(RuntimeError, 'FIXTURE_MODEL_IMPORT_FAILURE'):
            trial.run(c)
        self.assertEqual(self.trace, ['gate', 'prepare', 'sandbox', 'activate', 'torch', 'prove',
                                     'collect', 'synchronize', 'empty_cache', 'release'])
        cuda.synchronize.assert_called_once_with()
        cuda.empty_cache.assert_called_once_with()

    def test_trial_releases_only_lease_without_cross_task_sandbox_maintenance(self):
        c, cuda = self.cleanup_trial()
        self.gate.maintain_caches = Mock(side_effect=PermissionError('ORIGINAL_CONFIG_OUTSIDE_SANDBOX'))
        self.gate.release.side_effect = lambda: guard.Gate.release(self.gate)
        with self.assertRaisesRegex(RuntimeError, 'FIXTURE_MODEL_IMPORT_FAILURE'):
            trial.run(c)
        self.gate.maintain_caches.assert_not_called()
        self.gate.release.assert_not_called()
        self.gate.lease.release.assert_called_once_with()
        self.assertEqual(self.trace[-4:], ['collect', 'synchronize', 'empty_cache', 'release'])
        self.assertIsNone(self.original_lease.fd)
        contender = guard.Lease(self.original_lease.path)
        try:
            self.assertTrue(contender.acquire())
        finally:
            contender.release()

    def test_trial_cleanup_failure_keeps_original_lease(self):
        c, cuda = self.cleanup_trial(synchronize_error=RuntimeError('FIXTURE_SYNCHRONIZE_FAILED'))
        with self.assertRaisesRegex(RuntimeError, 'FIXTURE_SYNCHRONIZE_FAILED'):
            trial.run(c)
        self.assertEqual(self.trace[-1], 'collect')
        cuda.empty_cache.assert_not_called()
        self.gate.release.assert_not_called()
        self.assert_lease_held(self.original_lease)

    def test_trial_failed_empty_cache_keeps_original_lease(self):
        c, cuda = self.cleanup_trial()
        cuda.empty_cache.side_effect = RuntimeError('FIXTURE_EMPTY_CACHE_FAILED')
        with self.assertRaisesRegex(RuntimeError, 'FIXTURE_EMPTY_CACHE_FAILED'):
            trial.run(c)
        self.gate.release.assert_not_called()
        self.assert_lease_held(self.original_lease)

    def test_trial_failed_gc_keeps_original_lease(self):
        c, cuda = self.cleanup_trial()
        self.enter(patch.object(trial.gc, 'collect', side_effect=RuntimeError('FIXTURE_GC_FAILED')))
        with self.assertRaisesRegex(RuntimeError, 'FIXTURE_GC_FAILED'):
            trial.run(c)
        cuda.synchronize.assert_not_called()
        self.gate.release.assert_not_called()
        self.assert_lease_held(self.original_lease)

    def test_trial_unproven_torch_never_calls_cuda_or_releases_lease(self):
        c, cuda = self.cleanup_trial()
        self.prove.side_effect = ContractError('FIXTURE_UNVERIFIED_ACTUAL_HIP')
        with self.assertRaisesRegex(ContractError, 'FIXTURE_UNVERIFIED_ACTUAL_HIP'):
            trial.run(c)
        cuda.is_initialized.assert_not_called()
        cuda.synchronize.assert_not_called()
        cuda.empty_cache.assert_not_called()
        self.gate.release.assert_not_called()
        self.assert_lease_held(self.original_lease)

    def test_trial_cleanup_never_initializes_uninitialized_runtime(self):
        c, cuda = self.cleanup_trial()
        cuda.is_initialized.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'FIXTURE_MODEL_IMPORT_FAILURE'):
            trial.run(c)
        cuda.synchronize.assert_not_called()
        cuda.empty_cache.assert_not_called()
        self.assertIsNone(self.original_lease.fd)

    def assert_trial_failed_frames_released(self, chain=None):
        c, cuda = self.cleanup_trial()
        references = {}
        class Allocation:
            def __init__(self, name): references[name] = weakref.ref(self)
            def requires_grad_(self, value): return self
            def eval(self): return self
            def named_parameters(self): return []
        def inner_failure(features):
            raise ValueError('INNER_OPTIMIZER_FRAME')
        def failed_optimizer(features, targets, *, readout_profile=None):
            self.assertIsNone(readout_profile)
            if chain:
                try:
                    inner_failure(features)
                except ValueError as exc:
                    if chain == 'cause':
                        raise RuntimeError('FIXTURE_OPTIMIZER_FAILURE_WITH_LIVE_TENSORS') from exc
                    raise RuntimeError('FIXTURE_OPTIMIZER_FAILURE_WITH_LIVE_TENSORS')
            raise RuntimeError('FIXTURE_OPTIMIZER_FAILURE_WITH_LIVE_TENSORS')
        cuda.get_device_properties = lambda unused: SimpleNamespace(total_memory=8 * 1024**3)
        cuda.set_per_process_memory_fraction = lambda fraction: None
        cuda.reset_peak_memory_stats = lambda: None
        fake_torch = SimpleNamespace(cuda=cuda, set_num_threads=lambda count: None,
            manual_seed=lambda seed: None, cat=lambda features: Allocation('x'),
            tensor=lambda *args, **kwargs: Allocation('y'), float32='float32')
        fake_model = SimpleNamespace(require_pocket4_gpu=lambda: 'cuda', tensor_hash=lambda value: 'hash',
            load_verified_laya=lambda *args: (Allocation('raw'), None, 'base'))
        fake_training = SimpleNamespace(train_head=failed_optimizer,
            features_from_laya=lambda *args: (Allocation('feature'), {}))
        importer = builtins.__import__
        def imports(name, *args, **kwargs):
            if name == 'torch':
                self.trace.append('torch')
                return fake_torch
            if name == 'laya_cell.model': return fake_model
            if name == 'laya_cell.financial_value_train': return fake_training
            return importer(name, *args, **kwargs)
        self.enter(patch('builtins.__import__', side_effect=imports))
        original_read = trial.read
        self.enter(patch.object(trial, 'read', side_effect=lambda path:
            {} if str(path).endswith('/base-manifest.json') else original_read(path)))
        self.enter(patch.object(trial, 'selected_rows', return_value=[{'net_usd': '1'}]))
        def synchronized():
            self.assertEqual(set(references), {'raw', 'feature', 'x', 'y'})
            self.assertTrue(all(reference() is None for reference in references.values()))
            self.assert_lease_held(self.original_lease)
            self.trace.append('synchronize')
        cuda.synchronize.side_effect = synchronized
        with self.assertRaisesRegex(RuntimeError, 'FIXTURE_OPTIMIZER_FAILURE_WITH_LIVE_TENSORS'):
            trial.run(c)
        self.assertEqual(self.trace[-4:], ['collect', 'synchronize', 'empty_cache', 'release'])
        self.assertIsNone(self.original_lease.fd)

    def test_trial_drops_model_and_failed_optimizer_frames_before_releasing_lease(self):
        self.assert_trial_failed_frames_released()

    def test_trial_chained_cause_drops_inner_optimizer_frame(self):
        self.assert_trial_failed_frames_released('cause')

    def test_trial_chained_context_drops_inner_optimizer_frame(self):
        self.assert_trial_failed_frames_released('context')

    def test_forward_proves_actual_hip_after_gate_and_sandbox_before_model(self):
        c = self.worker_config()
        self.install_gate()
        with self.assertRaisesRegex(ContractError, 'FIXTURE_UNVERIFIED_ACTUAL_HIP'):
            worker.run(c, 'predict')
        self.assertEqual(self.trace, ['gate', 'prepare', 'sandbox', 'activate', 'torch', 'prove'])
        self.gate.release.assert_not_called()
        self.assertIn(self.context['readonly'][0], self.sandbox.call_args.args[0]['read_only_paths'])
        self.prove.assert_called_once_with(self.context, 'AFTER_TORCH_BEFORE_MODEL')

    def test_forward_denied_gate_never_loads_gpu_and_keeps_visible_hold(self):
        c = self.worker_config()
        self.install_gate(False)
        status = worker.run(c, 'predict')
        self.assertEqual(status['state'], 'HOLD_SHARED_RESOURCE_OR_SOURCE')
        self.assertEqual(self.trace, ['gate', 'release'])
        self.assertFalse(list(Path(c['forward_root']).glob('plans/*/predictions/*/commit.json')))

    def test_forward_cannot_substitute_another_guard_python(self):
        c = self.worker_config()
        c['python'] = '/other/python'
        self.install_gate()
        with self.assertRaisesRegex(ContractError, 'GUARD_PYTHON_CHANGED'):
            worker.run(c, 'predict')
        self.gate_type.assert_not_called()
        self.assertEqual(self.trace, [])

    def forward_lifecycle(self, failure=None):
        c = self.worker_config()
        self.install_gate()
        self.prove.side_effect = self.trace_call('prove', proof())
        references = {}
        class Allocation:
            def __init__(self, name):
                references[name] = weakref.ref(self)
                self.cycle = self
        def inner_failure(value):
            raise RuntimeError('INNER_MODEL_FRAME')
        class Predictor:
            def __init__(self, *args, **kwargs):
                self.raw = Allocation('raw')
                self.heads = {'candidate': Allocation('head')}
                references['predictor'] = weakref.ref(self)
                if failure == 'constructor':
                    raise RuntimeError('PREDICTOR_CONSTRUCTOR_FAILED')
            def step(self):
                if failure in ('cause', 'context'):
                    try:
                        inner_failure(self.raw)
                    except RuntimeError as exc:
                        if failure == 'cause':
                            raise OSError('PREDICTOR_STATUS_WRITE_FAILED') from exc
                        raise OSError('PREDICTOR_STATUS_WRITE_FAILED')
                if failure == 'status':
                    raise OSError('PREDICTOR_STATUS_WRITE_FAILED')
                return {'state': 'ONE_COMPLETED_PREDICTION'}
        self.enter(patch.object(prediction, 'Predictor', Predictor))
        lease = self.install_real_lease()
        collect = worker.gc.collect
        def collected():
            self.trace.append('collect')
            if failure == 'collect':
                raise RuntimeError('CLEANUP_COLLECT_FAILED')
            return collect()
        self.enter(patch.object(worker.gc, 'collect', side_effect=collected))
        def cleaned(phase):
            self.trace.append(phase)
            self.assertTrue(all(ref() is None for ref in references.values()), references)
            self.assert_lease_held(lease)
            if failure == phase:
                raise RuntimeError('CLEANUP_' + phase.upper() + '_FAILED')
        cuda = SimpleNamespace(is_initialized=Mock(return_value=failure != 'uninitialized_setup'),
            synchronize=Mock(side_effect=lambda: cleaned('synchronize')),
            empty_cache=Mock(side_effect=lambda: cleaned('empty_cache')),
            get_device_properties=lambda index: SimpleNamespace(total_memory=8 * 1024**3),
            set_per_process_memory_fraction=lambda fraction: None)
        def threads(count):
            if failure in ('setup', 'uninitialized_setup'):
                raise RuntimeError('TORCH_SETUP_FAILED')
        fake_torch = SimpleNamespace(cuda=cuda, set_num_threads=threads)
        self.enter(patch.dict(sys.modules, {'torch': fake_torch}))
        importer = builtins.__import__
        def imports(name, *args, **kwargs):
            if name == 'torch':
                self.trace.append('torch')
                return fake_torch
            return importer(name, *args, **kwargs)
        self.enter(patch('builtins.__import__', side_effect=imports))
        return c, cuda, references, lease

    def install_real_lease(self):
        lease = guard.Lease(self.root / 'heavy-worker.lock')
        self.addCleanup(lease.release)
        def acquire(*args):
            self.trace.append('gate')
            return lease.acquire()
        def release():
            self.trace.append('release')
            lease.release()
        self.gate.acquire.side_effect = acquire
        self.gate.release.side_effect = release
        self.gate.lease = SimpleNamespace(release=Mock(side_effect=release))
        return lease

    def assert_lease_held(self, lease):
        contender = guard.Lease(lease.path)
        try:
            self.assertFalse(contender.acquire(), 'another worker acquired the original heavy lease')
        finally:
            contender.release()

    def test_forward_success_destroys_models_before_sync_and_lease_release(self):
        c, cuda, references, lease = self.forward_lifecycle()
        self.assertEqual(worker.run(c, 'predict'), {'state': 'ONE_COMPLETED_PREDICTION'})
        self.assertEqual(self.trace[-4:], ['collect', 'synchronize', 'empty_cache', 'release'])
        self.assertIsNone(lease.fd)
        self.assertTrue(all(ref() is None for ref in references.values()))

    def assert_forward_escaping_frames_released(self, failure):
        c, cuda, references, lease = self.forward_lifecycle(failure)
        with self.assertRaisesRegex((RuntimeError, OSError), 'PREDICTOR_'):
            worker.run(c, 'predict')
        self.assertEqual(self.trace[-4:], ['collect', 'synchronize', 'empty_cache', 'release'])
        self.assertTrue(all(ref() is None for ref in references.values()))
        self.assertIsNone(lease.fd)

    def test_forward_status_failure_drops_predictor_traceback(self):
        self.assert_forward_escaping_frames_released('status')

    def test_forward_chained_cause_drops_inner_model_frame(self):
        self.assert_forward_escaping_frames_released('cause')

    def test_forward_chained_context_drops_inner_model_frame(self):
        self.assert_forward_escaping_frames_released('context')

    def test_forward_failed_constructor_drops_partial_model(self):
        self.assert_forward_escaping_frames_released('constructor')

    def assert_forward_cleanup_failure_keeps_lease(self, phase):
        c, cuda, references, lease = self.forward_lifecycle(phase)
        with self.assertRaisesRegex(RuntimeError, 'CLEANUP_'):
            worker.run(c, 'predict')
        self.gate.release.assert_not_called()
        self.assert_lease_held(lease)

    def test_forward_failed_gc_keeps_original_lease(self):
        self.assert_forward_cleanup_failure_keeps_lease('collect')

    def test_forward_failed_sync_keeps_original_lease(self):
        self.assert_forward_cleanup_failure_keeps_lease('synchronize')

    def test_forward_failed_empty_cache_keeps_original_lease(self):
        self.assert_forward_cleanup_failure_keeps_lease('empty_cache')

    def test_forward_failed_torch_setup_cleans_initialized_runtime_without_worker(self):
        c, cuda, references, lease = self.forward_lifecycle('setup')
        with self.assertRaisesRegex(RuntimeError, 'TORCH_SETUP_FAILED'):
            worker.run(c, 'predict')
        self.assertFalse(references)
        self.assertEqual(self.trace[-4:], ['collect', 'synchronize', 'empty_cache', 'release'])

    def test_forward_cleanup_never_initializes_uninitialized_runtime(self):
        c, cuda, references, lease = self.forward_lifecycle('uninitialized_setup')
        with self.assertRaisesRegex(RuntimeError, 'TORCH_SETUP_FAILED'):
            worker.run(c, 'predict')
        cuda.synchronize.assert_not_called()
        cuda.empty_cache.assert_not_called()
        self.assertEqual(self.trace[-2:], ['collect', 'release'])

    def test_forward_unproven_torch_never_calls_cuda_or_releases_lease(self):
        c, cuda, references, lease = self.forward_lifecycle()
        self.prove.side_effect = ContractError('FIXTURE_UNVERIFIED_ACTUAL_HIP')
        with self.assertRaisesRegex(ContractError, 'FIXTURE_UNVERIFIED_ACTUAL_HIP'):
            worker.run(c, 'predict')
        cuda.is_initialized.assert_not_called()
        cuda.synchronize.assert_not_called()
        cuda.empty_cache.assert_not_called()
        self.gate.release.assert_not_called()
        self.assert_lease_held(lease)

    def publication(self):
        p = {'plan_id': 'p', 'candidate_id': 'c', 'input_end_ms': T + 60000,
             'hip_runtime_manifest_sha256': HIP_SHA}
        r = {'request_id': 'r', 'started_at_ms': T - 100}
        payload = {'computed_at_ms': T - 10, 'values_usd': {'BUY': 0, 'SELL': 0}}
        self.enter(patch.object(prediction.time, 'time', return_value=T / 1000))
        self.enter(patch('laya_cell.financial_forward_witness.sign_record',
                         side_effect=lambda c, role, value: {**value, 'role_witness': {'signed_at_ms': T}}))
        return {'hip_runtime': copy.deepcopy(SELECTION)}, p, r, self.root / 'publication', payload

    def test_selected_prediction_cannot_publish_without_active_proven_runtime(self):
        args = self.publication()
        with self.assertRaisesRegex(ContractError, 'PUBLICATION_RUNTIME_NOT_BOUND'):
            prediction.publish_prediction(*args)
        self.assertFalse((args[3] / 'prediction.json').exists())
        self.assertFalse((args[3] / 'commit.json').exists())

    def test_changed_plan_runtime_fails_before_publication(self):
        args = self.publication()
        args[1]['hip_runtime_manifest_sha256'] = '2' * 64
        with self.assertRaisesRegex(ContractError, 'FROZEN_PLAN_RUNTIME_CHANGED'):
            prediction.publish_prediction(*args)
        self.assertFalse((args[3] / 'prediction.json').exists())

    def test_actual_publication_proof_is_inside_signed_prediction_bytes(self):
        args = self.publication()
        evidence = proof()
        with patch.object(hip, 'prove_before_publish', return_value=evidence) as prove:
            commit = prediction.publish_prediction(*args)
        prove.assert_called_once_with(args[0])
        stored = json.loads((args[3] / 'prediction.json').read_text())
        self.assertEqual(stored['hip_runtime_proof'], evidence)
        self.assertEqual(commit['prediction_sha256'], prediction.sha_file(args[3] / 'prediction.json'))
        self.assertNotIn('hip_runtime_proof', args[4])

    def test_proof_work_cannot_extend_fixed_publication_deadline(self):
        args = self.publication()
        with patch.object(hip, 'prove_before_publish', return_value=proof()), \
             patch.object(prediction.time, 'time', side_effect=[T / 1000, (T + 60000) / 1000]):
            with self.assertRaisesRegex(ContractError, 'EXPIRED_BEFORE_PUBLICATION'):
                prediction.publish_prediction(*args)
        self.assertFalse((args[3] / 'prediction.json').exists())


class FinancialHipProofTests(unittest.TestCase):
    def test_legacy_no_selected_runtime_keeps_original_record_format(self):
        hip.check_guard_selection({}, {})
        hip.check_publication_proof(None, None, 'train')
        hip.check_plan({}, {})

    def test_selected_proof_requires_publication_phase_target_and_manifest(self):
        hip.check_publication_proof(proof('train'), HIP_SHA, 'train')
        for evidence in (None, {}, proof(), {**proof('train'), 'manifest_sha256': '2' * 64},
                         {**proof('train'), 'phase': 'AFTER_TORCH_BEFORE_MODEL'}):
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ContractError, 'PUBLICATION_PROOF_BINDING'):
                hip.check_publication_proof(evidence, HIP_SHA, 'train')

    def test_publication_header_without_actual_library_evidence_is_rejected(self):
        evidence = {k: v for k, v in proof().items() if k in ('schema', 'phase', 'target', 'manifest_sha256')}
        with self.assertRaisesRegex(ContractError, 'PUBLICATION_PROOF_LIBRARIES'):
            hip.check_publication_proof(evidence, HIP_SHA, 'predict')

    def test_missing_required_library_or_invalid_mapped_identity_is_rejected(self):
        for name in ('libamdhip64.so', 'librocm_kpack.so', 'libhsa-runtime64.so'):
            evidence = proof()
            evidence['libraries'][name] = {'loaded': False}
            with self.subTest(name=name), self.assertRaisesRegex(ContractError, 'PUBLICATION_PROOF_LIBRARIES'):
                hip.check_publication_proof(evidence, HIP_SHA, 'predict')
        for key, value in (('inode', 0), ('inode', True), ('device_major', -1), ('sha256', 'unbound')):
            evidence = proof()
            evidence['libraries']['libamdhip64.so'][key] = value
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ContractError, 'PUBLICATION_PROOF_LIBRARIES'):
                hip.check_publication_proof(evidence, HIP_SHA, 'predict')

    def test_optional_comgr_is_not_an_extra_probe_or_admission_claim(self):
        evidence = proof()
        evidence['libraries']['libamd_comgr.so'] = {'loaded': False}
        hip.check_publication_proof(evidence, HIP_SHA, 'predict')
        for key in ('worker_admission_granted', 'extra_gpu_probe_performed'):
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, 'PUBLICATION_PROOF_LIBRARIES'):
                hip.check_publication_proof({**evidence, key: True}, HIP_SHA, 'predict')

    def test_selected_plan_rejects_prediction_missing_or_changed_runtime_proof(self):
        from laya_cell.tests.test_financial_forward import fixture, reseal
        from laya_cell.financial_forward_protocol import score_case
        for evidence in (None, proof('train'), {**proof(), 'manifest_sha256': '2' * 64}):
            args = list(fixture())
            args[0]['hip_runtime_manifest_sha256'] = HIP_SHA
            reseal(args[0])
            args[2]['plan_id'] = args[0]['plan_id']
            if evidence is not None:
                args[2]['hip_runtime_proof'] = evidence
            outcome = score_case(*args)
            self.assertEqual(outcome['status'], 'CENSORED')
            self.assertEqual(outcome['reason'], 'PREDICTION_HIP_RUNTIME_PROOF_MISMATCH')

    def test_selected_valid_runtime_preserves_original_fee_based_scoring(self):
        from laya_cell.tests.test_financial_forward import fixture, reseal
        from laya_cell.financial_forward_protocol import score_case
        args = list(fixture())
        args[0]['hip_runtime_manifest_sha256'] = HIP_SHA
        reseal(args[0])
        args[2].update(plan_id=args[0]['plan_id'], hip_runtime_proof=proof())
        outcome = score_case(*args)
        self.assertEqual(outcome['status'], 'OBSERVED')
        self.assertEqual(outcome['actual_net_usd'], -11.5)


if __name__ == '__main__':
    unittest.main()
