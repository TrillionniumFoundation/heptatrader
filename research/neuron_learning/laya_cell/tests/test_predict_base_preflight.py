"""Run predict's metadata checks without importing or constructing a model."""
import ast
import builtins
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType
import unittest
from unittest.mock import patch

from laya_cell import integrity, promotion, resident_store, resident_source_policy
from laya_cell import resident_training_v2 as v2, resident_training_v3 as v3
from laya_cell import resident_training_v4 as v4, resident_training_v5 as v5
from laya_cell import resident_training_v6 as v6, resident_training_v7 as v7
from laya_cell import resident_training_v8 as v8
from laya_cell import resident_price_input, resident_price_calibration
from laya_cell import resident_price_baselines, resident_parent_calibration


SOURCE = Path(__file__).resolve().parents[1] / 'resident_predict.py'


class ModelBoundary(Exception):
    pass


class PredictBasePreflightTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = self.root / 'base'
        self.base.mkdir()
        inventory = {}
        for name in sorted(integrity.REQUIRED):
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            data = (json.dumps({'model_type': 'modernbert', 'hidden_size': 768}).encode()
                    if name == 'encoder/config.json'
                    else b'SYNTHETIC_METADATA_NOT_MODEL_WEIGHTS')
            path.write_bytes(data)
            inventory[name] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        manifest = {'schema': 'heptatrader.laya-base.v1',
                    'repository': integrity.LAYA_REPO, 'revision': integrity.LAYA_REVISION,
                    'files': inventory}
        self.manifest_path = self.base / 'base-manifest.json'
        self.manifest_path.write_text(json.dumps(manifest))
        self.base_id = integrity.verify_snapshot(self.base, manifest)
        self.parent = self.artifact('parent', '0' * 64, self.base_id)
        self.candidate = self.artifact('candidate', self.parent['candidate_id'], self.base_id)
        evaluation = self.root / 'evaluation'
        evaluation.mkdir()
        self.plan_path = evaluation / 'plan.json'
        self.plan = {'candidate_id': self.candidate['candidate_id'],
                     'candidate_directory': str(self.root / 'candidate'),
                     'parent_directory': str(self.root / 'parent'),
                     'parent_id': self.parent['candidate_id'], 'trial': 1,
                     'rule': dict(promotion.RULE), 'start_ms': int(time.time() * 1000) + 300000}
        self.seal_plan()
        self.config = {'base_directory': str(self.base)}
        self.manifest_reads, self.loads, self.cells = [], [], []
        self.returned_base_id = self.base_id

        def read(path, *args, **kwargs):
            value = integrity.read_json(path, *args, **kwargs)
            if Path(path) == self.manifest_path:
                self.manifest_reads.append(value)
            return value

        def loader(directory, value):
            self.loads.append((directory, value))
            return object(), object(), self.returned_base_id

        def cell(*args):
            self.cells.append(args)
            raise ModelBoundary('stop before model construction or adapter loading')

        # Compile the complete production function, without executing its
        # module-level Torch/model imports. All metadata validators stay real.
        function = next(node for node in ast.parse(SOURCE.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == 'predict')
        namespace = {
            '__name__': 'laya_cell.predict_base_fixture', '__package__': 'laya_cell',
            'Path': Path, 'time': time, 'os': os, 'json': json,
            'ContractError': integrity.ContractError, 'read_json': read,
            'sha_file': integrity.sha_file, 'digest': integrity.digest,
            'atomic': resident_store.atomic, 'write_once': resident_store.write_once,
            'validate_parent': integrity.validate_candidate_artifact,
            'declared_profile': resident_source_policy.declared_profile,
            'training_v2': v2, 'training_v3': v3, 'training_v4': v4,
            'training_v5': v5, 'training_v6': v6, 'training_v7': v7, 'training_v8': v8,
            'price_input': resident_price_input, 'price_calibration': resident_price_calibration,
            'price_refs': resident_price_baselines, 'parent_calibration': resident_parent_calibration,
            'load_verified_laya': loader, 'ResidentDecisionCell': cell,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        self.predict = namespace['predict']

    def artifact(self, name, parent_id, base_id):
        directory = self.root / name
        directory.mkdir(exist_ok=True)
        adapter = directory / 'adapter.safetensors'
        adapter.write_bytes(b'SYNTHETIC_NOT_TENSORS')
        value = {'base_id': base_id, 'parent_id': parent_id,
                 'objective': {'schema': 'heptatrader.laya-resident-objective.v1'},
                 'adapter_sha256': integrity.sha_file(adapter)}
        value['candidate_id'] = integrity.digest(value)
        (directory / 'candidate.json').write_text(json.dumps(value))
        return value

    def seal_plan(self):
        self.plan['plan_id'] = integrity.digest({k: v for k, v in self.plan.items() if k != 'plan_id'})
        self.plan_path.write_text(json.dumps(self.plan))

    def call_predict(self):
        safe = ModuleType('safetensors')
        safe_torch = ModuleType('safetensors.torch')

        def no_weights(*args, **kwargs):
            self.fail('adapter or model weights must not be loaded')

        safe_torch.load_file = no_weights
        safe.torch = safe_torch
        original_import = builtins.__import__

        def metadata_only_import(name, *args, **kwargs):
            if name.split('.')[0] in {'torch', 'numpy', 'transformers', '_rocm_sdk_core'}:
                self.fail('real model dependency must not be imported: ' + name)
            return original_import(name, *args, **kwargs)

        # Both substitutions end before returning; no audit hooks, resource
        # limits, argv/path changes or persistent fake modules affect the suite.
        with patch.dict(sys.modules, {'safetensors': safe, 'safetensors.torch': safe_torch}), \
                patch('builtins.__import__', side_effect=metadata_only_import):
            return self.predict(self.config, self.plan_path, 0)

    def test_verified_loader_base_id_is_manifest_digest(self):
        manifest = integrity.read_json(self.manifest_path)
        self.assertEqual(integrity.verify_snapshot(self.base, manifest), integrity.digest(manifest))
        # Check the formula against the actual loader without importing its
        # model dependencies or replacing the snapshot integrity validation.
        tree = ast.parse((SOURCE.parent / 'model.py').read_text())
        loader = next(node for node in tree.body
                      if isinstance(node, ast.FunctionDef) and node.name == 'load_verified_laya')
        assignments = [node for node in ast.walk(loader) if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'base_id'
                               for target in node.targets)]
        self.assertEqual(len(assignments), 1)
        self.assertEqual(ast.unparse(assignments[0].value), 'verify_snapshot(directory, manifest)')
        self.assertEqual(ast.unparse(loader.body[-1].value), '(model, tokenizer, base_id)')

    def test_mismatched_base_rejects_before_model_loader(self):
        self.candidate = self.artifact('candidate', self.parent['candidate_id'], 'f' * 64)
        self.plan['candidate_id'] = self.candidate['candidate_id']
        self.seal_plan()
        with self.assertRaisesRegex(integrity.ContractError, 'BASE_CHANGED'):
            self.call_predict()
        self.assertEqual(self.loads, [])
        self.assertEqual(self.cells, [])

    def test_valid_manifest_read_once_and_passed_unchanged_to_loader(self):
        with self.assertRaises(ModelBoundary):
            self.call_predict()
        self.assertEqual(len(self.loads), 1)
        self.assertEqual(self.loads[0][0], self.base)
        self.assertEqual(len(self.manifest_reads), 1)
        self.assertIs(self.loads[0][1], self.manifest_reads[0])
        self.assertEqual(len(self.cells), 1)

    def test_postload_base_mismatch_still_rejected(self):
        self.returned_base_id = 'e' * 64
        with self.assertRaisesRegex(integrity.ContractError, 'BASE_CHANGED'):
            self.call_predict()
        self.assertEqual(len(self.loads), 1)
        self.assertEqual(self.cells, [])


if __name__ == '__main__':
    unittest.main()
