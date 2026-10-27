"""Real input-file admission, stopped before model loading or training."""
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from safetensors.numpy import save_file

import test_laya_resident_action_v2_closed_source as original
from laya_cell import resident_closed_action_training as closed
from laya_cell import resident_train as worker
from laya_cell import resident_training_v4 as v4
from laya_cell import resident_training_v8 as v8
from laya_cell import shadow_source
from laya_cell.dataset import rows_for_training
from laya_cell.integrity import ContractError, canonical, digest, read_json, sha_file
from laya_cell.tests import test_shadow_source as quote_fixture
from laya_cell.tests.test_resident_price_calibration import real_dataset_population


class _InputsAccepted(Exception):
    """The fixture must never proceed to model loading or an optimizer."""


class JobInputValidationTests(unittest.TestCase):
    def setUp(self):
        self.original = original.ClosedOriginalParentSourceTests()
        self.original.setUp()
        self.addCleanup(self.original.doCleanups)
        self.root = self.original.root
        parent_dir = self.root / 'parent'
        parent_dir.mkdir()
        self.adapter = parent_dir / 'adapter.safetensors'
        save_file({'fixture_only': np.zeros(4, dtype=np.float32)}, str(self.adapter))
        parent = {'adapter_sha256': sha_file(self.adapter), 'trained_until_ms': 0}
        parent['candidate_id'] = digest(parent)
        (parent_dir / 'candidate.json').write_bytes(canonical(parent) + b'\n')
        for path in self.original.dir.glob('prediction-*.json'):
            prediction = read_json(path)
            prediction['parent_id'] = parent['candidate_id']
            path.write_bytes(canonical(prediction) + b'\n')
            outcome_path = path.with_name(path.name.replace('prediction-', 'outcome-'))
            outcome = read_json(outcome_path)
            outcome['prediction_file_sha256'] = sha_file(path)
            outcome_path.write_bytes(canonical(outcome) + b'\n')
        source = closed.freeze_source(self.root, parent['candidate_id'], original.CUTOFF)
        self.source_path = self.root / 'closed-source.json'
        source_sha = closed.write_source(self.source_path, source)

        dataset, old_window, _ = real_dataset_population()
        shift = original.CUTOFF - old_window['training_end_ms']
        for sample in dataset['samples']:
            for key in ('anchor_ms', 'known_at_ms'):
                sample[key] += shift
            for quote in sample['input_snapshot']['quotes']:
                quote['t'] += shift
            sample['sample_id'] = digest(sample['input_snapshot'])
            for key in ('observed_at_ms', 'available_at_ms'):
                sample['outcome_receipts'][0][key] += shift
        # Exercise the ordinary byte-proof check too, with a synthetic source
        # chain. This is an admission fixture, not a market/model result.
        quotes = quote_fixture.ShadowEvidenceTests()
        quotes.setUp()
        self.addCleanup(quotes.doCleanups)
        quotes.write()
        dataset['source_prefixes'] = [shadow_source.read_source(quotes.path)[2]]
        window = {'training_end_ms': original.CUTOFF,
                  'validation_start_ms': original.CUTOFF + 1,
                  'validation_end_ms': original.CUTOFF + 86400000}
        rows, _ = rows_for_training(dataset, window)
        rows = v8.timed_rows(rows, dataset)
        ids = v8.check_training_rows(rows, cutoff_ms=original.CUTOFF)
        dataset_path = self.root / 'dataset.json'
        dataset_path.write_bytes(canonical(dataset) + b'\n')
        baseline = self.root / 'baseline'
        baseline.mkdir()
        (baseline / 'model.json').write_bytes(canonical({'model_id': 'synthetic-admission'}) + b'\n')
        (baseline / 'freeze-manifest.json').write_bytes(canonical({
            'name': 'heptatrader-bootstrap-mlp-v1', 'learner_disabled': True,
            'selected_model_id': 'synthetic-admission',
            'inventory_sha256': {'model.json': sha_file(baseline / 'model.json')}}) + b'\n')
        self.job = {
            'objective': deepcopy(v8.OBJECTIVE), 'profile': worker.PROFILE,
            'base_directory': str(baseline), 'baseline_directory': str(baseline),
            'output_directory': str(self.root / 'uncreated-candidate'),
            'parent_directory': str(parent_dir), 'parent_id': parent['candidate_id'],
            'parent_manifest_sha256': sha_file(parent_dir / 'candidate.json'),
            'dataset': str(dataset_path), 'dataset_sha256': sha_file(dataset_path),
            'window': window, 'training_row_ids': ids, 'data_fingerprint': digest(ids),
            'training_row_accounting_schema': v8.ROW_ACCOUNTING_SCHEMA,
            'evaluation_directory': str(self.root),
            'closed_action_source': str(self.source_path), 'closed_action_source_sha256': source_sha,
            'closed_action_source_id': source['source_id'],
            'closed_action_parent_id': source['parent_id'],
            'closed_action_training_cutoff_ms': original.CUTOFF,
        }

    def admit(self, job=None):
        with patch.object(worker, 'require_pocket4_gpu', side_effect=_InputsAccepted), \
                patch.object(worker, 'load_verified_laya', side_effect=AssertionError('model loading forbidden')):
            worker.run_job(self.job if job is None else job, old_learner_verified=True)

    def test_v8_worker_uses_one_checked_source_before_model_loading(self):
        counts = defaultdict(Counter)
        original_open = Path.open
        tracked = {self.source_path, self.adapter}

        class Reader:
            def __init__(self, stream, path):
                self.stream, self.path = stream, path
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def read(self, *args, **kwargs):
                value = self.stream.read(*args, **kwargs)
                counts[self.path]['bytes'] += len(value)
                return value
            def __getattr__(self, name):
                return getattr(self.stream, name)

        def opening(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if path not in tracked:
                return stream
            counts[path]['opens'] += 1
            return Reader(stream, path)

        with patch.object(Path, 'open', opening), \
                patch.object(closed, 'collect_parent_closed_train', wraps=closed.collect_parent_closed_train) as replay:
            with self.assertRaises(_InputsAccepted):
                self.admit()
        # One source SHA read plus one strict JSON parse, with real original
        # replay. The separate worker parent check remains at its own boundary.
        self.assertEqual(counts[self.source_path], {'opens': 2, 'bytes': 2*self.source_path.stat().st_size})
        self.assertEqual(counts[self.adapter], {'opens': 2, 'bytes': 2*self.adapter.stat().st_size})
        self.assertEqual(replay.call_count, 1)
        self.assertFalse(Path(self.job['output_directory']).exists())

    def test_original_change_is_not_reused_by_another_worker_or_candidate_verification(self):
        with self.assertRaises(_InputsAccepted):
            self.admit()
        path = self.original.dir / 'prediction-000.json'
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ContractError, 'PREDICTION_CHANGED'):
            self.admit()
        # Candidate acceptance is a new boundary, even after this process
        # already validated the same job. Its original replay must still fail.
        with self.assertRaisesRegex(ContractError, 'PREDICTION_CHANGED'):
            v8.verify_candidate(self.job, {})

    def test_both_episode_branches_keep_job_binding_checks(self):
        for policy in (v4, v8):
            changed = deepcopy(self.job)
            changed['objective'] = deepcopy(policy.OBJECTIVE)
            changed['data_fingerprint'] = 'f' * 64
            with self.subTest(policy=policy.SCHEMA), self.assertRaisesRegex(ContractError, 'EPISODE_ROW_FINGERPRINT'):
                self.admit(changed)


if __name__ == '__main__':
    unittest.main()
