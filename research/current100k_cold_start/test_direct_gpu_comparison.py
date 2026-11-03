"""Protocol tests use model fixtures; they make no hardware parity claim."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location('direct_gpu_comparison', Path(__file__).with_name('direct_gpu_comparison.py'))
direct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(direct)


class FakeModel:
    def requires_grad_(self, value):
        assert value is False
        return self
    def eval(self):
        return self


class CPUResult:
    def __init__(self, data): self.data = data
    def tolist(self): return self.data


class DirectChildBoundaries(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.output = self.root/'direct-baseline'; self.output.mkdir()
        old_path = list(sys.path); self.addCleanup(lambda: sys.path.__setitem__(slice(None), old_path))
        self.diag = direct.load_helpers(); self.bench = self.diag.bench
        self.heavy = self.root/'heavy.lock'; self.fd = os.open(self.heavy, os.O_CREAT|os.O_RDWR, 0o600)
        self.addCleanup(os.close, self.fd); fcntl.flock(self.fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.events = []; self.api = {'test_fixture_not_hardware': True}
        self.base = self.root/'base'; self.base.mkdir(); self.write(self.base/'base-manifest.json', {})
        candidate_dir = self.root/'candidate'; candidate_dir.mkdir()
        (candidate_dir/'value-head.safetensors').write_bytes(b'fixture-head-not-real-tensors')
        self.config = {'worker_source_root': str(self.root/'selected-source'), 'forward_root': str(self.root/'live-forward'),
            'hip_runtime': {'test_fixture_not_hardware': True}, 'worker_read_only_paths': [],
            'period_path': str(self.root/'period.json'), 'oms_path': str(self.root/'oms.json'),
            'attribution_directory': str(self.root/'attribution'), 'role_key_path': str(self.root/'non-readable-role-key'),
            'base_directory': str(self.base)}
        self.plan = {'plan_id': 'plan', 'candidate_directory': str(candidate_dir),
            'head_tensor_sha256': self.bench.sha(candidate_dir/'value-head.safetensors'),
            'training_dataset_path': str(self.root/'training.json')}
        self.write(self.root/'config.json', self.config); self.write(self.root/'plan.json', self.plan)
        self.original = {'scope': direct.SCOPE, 'source_sha256': direct.source_pins(), 'parent_pid': os.getppid(),
            'heavy_lock_path': str(self.heavy), 'heavy_lock_identity': self.bench.lock_identity(self.fd),
            'threads': 2, 'config_path': str(self.root/'config.json'), 'config_sha256': self.bench.sha(self.root/'config.json'),
            'plan_path': str(self.root/'plan.json'), 'plan_sha256': self.bench.sha(self.root/'plan.json'),
            'plan': self.plan, 'candidate': {'candidate_id': 'candidate', 'base_id': 'base'}, 'model_api': self.api,
            'request_item': {'request': {'request_id': 'historical-request'}, 'request_sha256': 'request-sha',
                             'producer_manifest_sha256': 'producer', 'source_record_sha256': 'source-record'}}
        self.staged = {'scope': direct.SCOPE, 'phase': 'STAGED_GPU_INFERENCE_COMPLETE',
            'config_sha256': self.original['config_sha256'], 'source_sha256': direct.source_pins(),
            'plan_id': 'plan', 'candidate_id': 'candidate', 'request_id': 'historical-request', 'request_sha256': 'request-sha',
            'forward_seal_created': False, 'broker_orders_sent': 0, 'direct_loader_parity_proven': False}
        self.state = {'cpu_initial': {'model/p': {'sha256': 'a'}}, 'staged_after': {'model/p': {'sha256': 'a'}},
                      'cpu_tokenizer': [{'input_ids': {'sha256': 'buy'}}, {'input_ids': {'sha256': 'sell'}}]}
        self.proof = {'scope': direct.SCOPE, 'source_sha256': direct.source_pins(),
            'original_input_proof_path': str(self.root/'input-proof.json'),
            'staged_result_path': str(self.root/'staged-result.json'), 'staged_state_path': str(self.root/'staged-state.json'),
            'output_dir': str(self.output), 'parent_pid': os.getppid(), 'heavy_lock_path': str(self.heavy),
            'heavy_lock_identity': self.bench.lock_identity(self.fd), 'checked_ns': time.monotonic_ns(),
            'admission': {'role': 'value-predictor', 'admit_new_worker': True, 'reasons': [], 'at_ms': int(time.time()*1000)}}
        self.proof_path = self.root/'direct-proof.json'; self.seal()
        self.stack.enter_context(patch.object(direct, 'arm_parent_death'))
        self.stack.enter_context(patch.object(direct, 'load_helpers', return_value=self.diag))
        self.stack.enter_context(patch.object(self.bench, 'model_api', return_value=self.api))
        self.stack.enter_context(patch.object(self.bench, 'load_benchmark_head', return_value=FakeModel()))
        self.stack.enter_context(patch.object(self.diag, 'model_state', return_value=self.state['cpu_initial']))
        self.stack.enter_context(patch.object(self.diag, 'tokenizer_state', return_value=self.state['cpu_tokenizer']))
        # This executor does not expose /proc/<pid>/fd. Production still calls
        # the unmodified fail-closed observation; the flock below is real.
        self.stack.enter_context(patch.object(self.diag, 'fd_snapshot', return_value=[]))
        def inference(*args):
            self.events.append('inference')
            return CPUResult([[0.0]*768, [0.0]*768]), CPUResult([-1., -2.]), {
                'completed_ns': time.monotonic_ns(), 'inference_ms': .1, 'test_fixture_not_hardware': True}
        self.stack.enter_context(patch.object(self.diag, 'infer', side_effect=inference))
        self.setup_modules()

    def write(self, path, value):
        Path(path).write_text(json.dumps(value, sort_keys=True))

    def seal(self):
        for key, content in (('original_input_proof', self.original), ('staged_result', self.staged), ('staged_state', self.state)):
            self.write(self.proof[key+'_path'], content)
            self.proof[key+'_sha256'] = self.bench.sha(self.proof[key+'_path'])
        self.write(self.proof_path, self.proof)
        self.proof_sha = self.bench.sha(self.proof_path)

    def setup_modules(self):
        package = ModuleType('laya_cell'); package.__path__ = []
        hip = ModuleType('laya_cell.resident_hip_runtime')
        hip.prepare_worker = lambda *args: {'readonly': []}
        hip.activate = lambda *args: self.events.append('hip_activate')
        hip.prove = lambda *args: {'test_fixture_not_hardware': True}
        sandbox = ModuleType('sandbox')
        def enter(config):
            self.sandbox_config = config; self.events.append('sandbox')
            return {'test_fixture_not_hardware': True}
        sandbox.enter = enter
        torch = ModuleType('torch'); torch.__version__ = 'fixture-no-hardware'; torch.version = SimpleNamespace(hip='fixture')
        torch.set_num_threads = lambda value: self.events.append(('threads', value))
        torch.cuda = SimpleNamespace(is_initialized=lambda: False, get_device_properties=lambda n: SimpleNamespace(total_memory=8*1024**3),
            set_per_process_memory_fraction=lambda fraction: self.events.append(('gpu_fraction', fraction)),
            reset_peak_memory_stats=lambda: None, synchronize=lambda: None,
            max_memory_allocated=lambda: 1234, max_memory_reserved=lambda: 2345)
        model = ModuleType('laya_cell.model')
        def require_gpu():
            self.events.append('first_gpu'); return SimpleNamespace(type='cuda')
        self.require_gpu = Mock(side_effect=require_gpu); model.require_pocket4_gpu = self.require_gpu
        def load(*args, enforce_gpu):
            self.assertTrue(enforce_gpu); self.events.append('load_model')
            return FakeModel(), object(), 'base'
        self.load_model = Mock(side_effect=load); model.load_verified_laya = self.load_model
        value = ModuleType('laya_cell.financial_value_train')
        forward = ModuleType('laya_cell.current100k_forward'); forward.feature_rows = lambda *args: [{'side': 'BUY'}, {'side': 'SELL'}]
        safe = ModuleType('safetensors'); safe.__path__ = []
        safe_torch = ModuleType('safetensors.torch'); safe_torch.load_file = lambda path: {}
        modules = {'laya_cell': package, 'laya_cell.resident_hip_runtime': hip, 'laya_cell.model': model,
            'laya_cell.financial_value_train': value, 'laya_cell.current100k_forward': forward,
            'sandbox': sandbox, 'torch': torch, 'safetensors': safe, 'safetensors.torch': safe_torch}
        self.stack.enter_context(patch.dict(sys.modules, modules))

    def run_child(self):
        return direct.run_direct(self.proof_path, self.proof_sha, self.fd)

    def test_success_pins_inputs_preserves_heavy_fd_and_records_uncompared_result(self):
        result = self.run_child()
        self.assertLess(self.events.index('sandbox'), self.events.index('hip_activate'))
        self.assertLess(self.events.index('hip_activate'), self.events.index('first_gpu'))
        self.assertLess(self.events.index('first_gpu'), self.events.index('load_model'))
        self.assertEqual(result['direct_proof_sha256'], self.proof_sha)
        self.assertEqual(result['staged_result_sha256'], self.proof['staged_result_sha256'])
        self.assertEqual(result['initial_state'], self.state['cpu_initial'])
        self.assertEqual(result['values'], [-1., -2.]); self.assertFalse(result['direct_loader_parity_proven'])
        self.assertFalse(result['forward_seal_created']); self.assertEqual(result['broker_orders_sent'], 0)
        self.assertFalse(result['global_runtime_cache_reset']); self.assertFalse(os.get_inheritable(self.fd))
        self.assertEqual(self.bench.read(self.output/'result.json'), result)
        for key in ('original_input_proof_path', 'staged_result_path', 'staged_state_path'):
            self.assertIn(self.proof[key], self.sandbox_config['read_only_paths'])
        self.assertEqual(self.sandbox_config['output_dir'], str(self.output))
        other = os.open(self.heavy, os.O_RDWR)
        try:
            with self.assertRaises(BlockingIOError): fcntl.flock(other, fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally: os.close(other)

    def test_staged_file_changed_fails_before_model(self):
        Path(self.proof['staged_result_path']).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'PINNED_JSON_CHANGED'): self.run_child()
        self.require_gpu.assert_not_called()

    def test_same_sha_valid_file_with_other_request_is_rejected(self):
        self.staged['request_id'] = 'different-request'; self.seal()
        with self.assertRaisesRegex(ValueError, 'STAGED_IDENTITY_CHANGED'): self.run_child()
        self.require_gpu.assert_not_called()

    def test_other_source_inventory_is_rejected(self):
        self.proof['source_sha256']['direct_gpu_comparison.py'] = '0'*64; self.seal()
        with self.assertRaisesRegex(ValueError, 'SOURCE_OR_SCOPE_CHANGED'): self.run_child()
        self.require_gpu.assert_not_called()

    def test_existing_output_cannot_be_reused(self):
        (self.output/'old-result').write_text('old')
        with self.assertRaisesRegex(ValueError, 'NEW_EMPTY_OUTPUT_DIRECTORY_REQUIRED'): self.run_child()

    def test_denied_original_guard_never_loads_model(self):
        self.proof['admission']['reasons'] = ['SOURCE_HOLD']; self.seal()
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_GUARD_DENIED'): self.run_child()
        self.require_gpu.assert_not_called(); self.assertEqual(self.events, [])

    def test_expiry_during_setup_is_rechecked_before_first_gpu(self):
        calls = 0; original_admission = self.diag.admission
        def check(report, stamp):
            nonlocal calls
            calls += 1
            if calls == 2: raise ValueError('ORIGINAL_ADMISSION_EXPIRED')
            original_admission(report, stamp)
        with patch.object(self.diag, 'admission', side_effect=check):
            with self.assertRaisesRegex(ValueError, 'ORIGINAL_ADMISSION_EXPIRED'): self.run_child()
        self.require_gpu.assert_not_called(); self.load_model.assert_not_called()

    def test_role_key_readability_aborts_before_gpu(self):
        Path(self.config['role_key_path']).write_text('fixture-key')
        with self.assertRaisesRegex(ValueError, 'ROLE_KEY_READABLE'): self.run_child()
        self.require_gpu.assert_not_called(); self.assertEqual(self.events, ['sandbox'])

    def test_replaced_heavy_path_aborts_before_gpu(self):
        self.heavy.rename(self.root/'old-heavy.lock'); self.heavy.touch(mode=0o600)
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_HEAVY_PATH_CHANGED'): self.run_child()
        self.require_gpu.assert_not_called()

    def test_cannot_make_staged_input_writable_via_output(self):
        inside = self.output/'staged-result.json'; self.proof['staged_result_path'] = str(inside); self.seal()
        with self.assertRaisesRegex(ValueError, 'PINNED_INPUTS_MUST_BE_OUTSIDE'): self.run_child()


class PureImportAndParentBoundaries(unittest.TestCase):
    def test_import_does_not_import_torch_transformers_or_laya(self):
        script = '''import importlib.util,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('direct',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
assert not any(name in sys.modules for name in ('torch','transformers','laya','laya_cell'))
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, str(Path(direct.__file__))], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_parent_fails_before_prctl(self):
        with patch.object(direct.ctypes, 'CDLL') as libc:
            with self.assertRaisesRegex(ValueError, 'ADMITTING_PARENT_REQUIRED'):
                direct.arm_parent_death(os.getppid()+100000)
            libc.assert_not_called()


if __name__ == '__main__':
    unittest.main()
