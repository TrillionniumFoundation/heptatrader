"""Boundary tests without Torch, live Guard configuration, or broker access."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('cold_benchmark', Path(__file__).with_name('benchmark.py'))
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.plan = {'plan_id': 'plan', 'behavior_scope': {'behavior_model_id': 'actor'},
                     'producer_registration': {'sha256': 'producer'}}
        self.item = {'request': {'request_id': 'request', 'started_at_ms': 10,
                    'model_id': 'actor', 'position_at_request': 0},
                    'request_sha256': 'request-sha', 'producer_manifest_sha256': 'producer'}

    def test_only_existing_historical_requests_eligible(self):
        self.assertEqual(bench.select_request({'request': self.item}, 'latest-flat', self.plan, 11), self.item)
        with self.assertRaises(ValueError):
            bench.select_request({'request': self.item}, 'latest-flat', self.plan, 10)
        with self.assertRaises(KeyError):
            bench.select_request({'request': self.item}, 'fabricated-request', self.plan, 11)

    def test_changed_scope_and_nonflat_rejected(self):
        for field, value in [('model_id', 'different-actor'), ('position_at_request', 1)]:
            changed = {**self.item, 'request': {**self.item['request'], field: value}}
            with self.assertRaises(ValueError):
                bench.select_request({'request': changed}, 'request', self.plan, 11)

    def test_stable_request_choice_does_not_replace_explicit_request(self):
        latest = {**self.item, 'request': {**self.item['request'], 'request_id': 'new', 'started_at_ms': 15}}
        requests = {'request': self.item, 'new': latest}
        self.assertEqual(bench.select_request(requests, 'request', self.plan, 20), self.item)
        self.assertEqual(bench.select_request(requests, 'latest-flat', self.plan, 20), latest)

    def test_proof_is_exclusive_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'proof.json'
            bench.write(path, {'proof': True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                bench.write(path, {'proof': False})
            self.assertEqual(json.loads(path.read_text()), {'proof': True})

    def test_lock_identity_rejects_public_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'lock'
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                self.assertEqual(len(bench.lock_identity(fd)), 2)
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    bench.lock_identity(fd)
            finally:
                os.close(fd)

    def test_original_guard_denial_cannot_start_model_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forward = root/'forward'
            (forward/'plans'/'p').mkdir(parents=True)
            bench.write(forward/'plans'/'p'/'plan.json', self.plan)
            guard_path = root/'guard.json'
            bench.write(guard_path, {})
            config = {'forward_root': str(forward), 'guard_config': str(guard_path),
                      'guard_config_sha256': bench.sha(guard_path), 'period_sha256': 'period'}
            gate = Mock(last_admission={'admit_new_worker': False, 'reasons': ['SOURCE_NOT_READY']})
            module_values = {
                'laya_cell.current100k_forward_worker': {
                    'validate_runtime': Mock(), 'acquire_value_resources': Mock(return_value=False)},
                'laya_cell.financial_forward_evaluate': {'validate_registration': Mock(return_value={})},
                'laya_cell.financial_forward_source': {'ingress': Mock(return_value=(None, {'request': self.item}, [], []))},
                'laya_cell.resident_hip_runtime': {'check_guard_selection': Mock()},
                'historical_training.resident_resource_guard': {'Gate': Mock(return_value=gate), 'verify_config': Mock(return_value={})}}
            modules = {name: ModuleType(name) for name in ('laya_cell', 'historical_training', *module_values)}
            for name, values in module_values.items():
                vars(modules[name]).update(values)
            modules['laya_cell'].resident_hip_runtime = modules['laya_cell.resident_hip_runtime']
            args = SimpleNamespace(config=root/'unused.json', sha256='unused', request_id='request',
                                   request_sha256=None, output=root/'diagnostic')
            with patch.dict(sys.modules, modules), patch.object(bench, 'bootstrap', return_value=config), \
                    patch.object(bench, 'model_api', return_value={'readout_mode': 'TEST_NOT_GPU'}), \
                    patch.object(bench.subprocess, 'Popen') as spawn:
                self.assertEqual(bench.parent(args), 3)
                spawn.assert_not_called()
            gate.release.assert_called_once()
            self.assertEqual(bench.read(args.output/'report.json')['state'], 'HELD_ORIGINAL_GUARD_NO_MODEL_LOADED')

    def test_current_source_uses_explicit_head_helpers(self):
        root = Path(__file__).resolve().parents[1]/'neuron_learning'/'laya_cell'
        paths = {name: root/(name+'.py') for name in ('model', 'financial_value_train', 'financial_forward_predict')}
        result = bench.model_api_from_files(paths, {'readout_profile': None})
        self.assertEqual(result['readout_mode'], 'FROZEN_FINANCIAL_VALUE_TRAIN_HEAD_HELPERS')

    def test_unknown_legacy_shape_fails_before_model_import(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {name: Path(directory)/(name+'.py') for name in
                     ('model', 'financial_value_train', 'financial_forward_predict')}
            paths['model'].write_text('def load_verified_laya(): pass\ndef require_pocket4_gpu(): pass\n')
            paths['financial_value_train'].write_text('def features_from_laya(): pass\nclass FinancialHead: pass\n')
            paths['financial_forward_predict'].write_text('class Predictor: pass\n')
            with self.assertRaisesRegex(ValueError, 'FROZEN_READOUT_API_UNSUPPORTED'):
                bench.model_api_from_files(paths, {})

    def test_legacy_readout_does_not_gain_new_clipping_semantics(self):
        api = {'readout_mode': 'EXACT_RELEASE_258_INLINE_FINANCIAL_HEAD_UNCLAMPED_X100'}
        self.assertEqual(bench.benchmark_values(None, lambda x: x, .2, api, None), 20.)
        self.assertEqual(bench.benchmark_values(None, lambda x: x, -.3, api, None), -30.)


if __name__ == '__main__':
    unittest.main()
