"""Parent manifest pins fail before data collection or model allocation."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from laya_cell.integrity import ContractError, sha_file
from laya_cell import resident_train as training


class ParentManifestLineageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.parent = self.root / 'parent'
        self.parent.mkdir()
        self.manifest = self.parent / 'candidate.json'
        self.manifest.write_text('{"candidate_id":"same-parent","metadata":1}\n')
        self.dataset = self.root / 'dataset.json'
        self.dataset.write_text('{}\n')
        self.config = {
            'objective': copy.deepcopy(training.OBJECTIVE), 'profile': training.PROFILE,
            'base_directory': str(self.root / 'base'),
            'output_directory': str(self.root / 'new-candidate'),
            'baseline_directory': str(self.root / 'baseline'),
            'parent_directory': str(self.parent), 'parent_id': 'same-parent',
            'parent_manifest_sha256': sha_file(self.manifest),
            'dataset': str(self.dataset), 'dataset_sha256': sha_file(self.dataset),
        }

    def test_changed_manifest_rejected_before_dataset_and_gpu(self):
        self.manifest.write_text('{"candidate_id":"same-parent","metadata":2}\n')
        with patch.object(training, 'verify_baseline', return_value='baseline'), \
             patch.object(training, 'validate_parent', return_value={'candidate_id':'same-parent'}), \
             patch.object(training, 'read_json', side_effect=AssertionError('DATASET_REACHED')) as data, \
             patch.object(training, 'require_pocket4_gpu', side_effect=AssertionError('GPU_REACHED')) as gpu:
            with self.assertRaisesRegex(ContractError, 'PARENT_MANIFEST_CHANGED'):
                training.run_job(self.config, old_learner_verified=True)
        data.assert_not_called()
        gpu.assert_not_called()

    def test_matching_pin_and_legacy_jobs_continue_to_original_dataset_validation(self):
        for pinned in (True, False):
            with self.subTest(pinned=pinned):
                config = dict(self.config)
                if not pinned:
                    config.pop('parent_manifest_sha256')
                with patch.object(training, 'verify_baseline', return_value='baseline'), \
                     patch.object(training, 'validate_parent', return_value={'candidate_id':'same-parent'}), \
                     patch.object(training, 'read_json', side_effect=RuntimeError('FULL_DATASET_VALIDATION_REACHED')) as data, \
                     patch.object(training, 'require_pocket4_gpu', side_effect=AssertionError('GPU_REACHED')) as gpu:
                    with self.assertRaisesRegex(RuntimeError, 'FULL_DATASET_VALIDATION_REACHED'):
                        training.run_job(config, old_learner_verified=True)
                data.assert_called_once_with(self.dataset, 64*1024**2)
                gpu.assert_not_called()


if __name__ == '__main__':
    unittest.main()
