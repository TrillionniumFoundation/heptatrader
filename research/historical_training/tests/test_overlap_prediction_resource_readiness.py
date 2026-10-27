"""A new overlapping trial must check real prediction headroom before allocation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from historical_training.common import EvidenceError, sha
from historical_training.resident_resource_guard import GIB, Gate


class PredictionReadinessTests(unittest.TestCase):
    def test_forward_overlap_uses_heavy_memory_floor_without_source_refresh(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            original = root / 'original.json'; original.write_text('{}')
            period = root / 'period.json'; period.write_text('{}')
            gate = Gate.__new__(Gate)
            gate.role = 'trainer'; gate.root = root; gate.draining = False
            gate.c = {'original_config': str(original), 'original_config_sha256': sha(original),
                      'period_path': str(period), 'period_sha256': sha(period),
                      'manual_hold_path': str(root / 'absent-hold')}
            memory = {'MemTotal': 24 * GIB, 'MemAvailable': 7 * GIB,
                      'SwapTotal': 8 * GIB, 'SwapFree': 0}
            original_read_text = Path.read_text
            def telemetry(path, *args, **kwargs):
                if str(path) == '/proc/meminfo':
                    return ''.join(f'{key}: {value//1024} kB\n' for key, value in memory.items())
                if str(path) == '/proc/pressure/memory':
                    return 'full avg10=0 avg60=0 avg300=0 total=0\n'
                return original_read_text(path, *args, **kwargs)
            with patch('historical_training.resident_resource_guard.parse_meminfo', return_value=memory), \
                 patch.object(Path, 'read_text', new=telemetry), \
                 patch('historical_training.resident_resource_guard.collect_resource_attribution',
                       return_value={'diagnostic_only': True, 'partial': True}), \
                 patch('historical_training.resident_resource_guard.parse_full_psi',
                       return_value={'avg10': 0., 'avg60': 0.}), \
                 patch('historical_training.resident_live_source.refresh',
                       side_effect=AssertionError('NO_SOURCE_SCAN_WHILE_RESOURCE_DENIED')):
                report = gate.check(forward_registration={'job_id': 'job', 'candidate_id': 'candidate'},
                                    prediction_ready=True)
            self.assertFalse(report['admit_new_worker'])
            self.assertEqual(report['resource_role'], 'evaluator')
            self.assertIn('HOST_AVAILABLE_MEMORY_BELOW_8GIB', report['reasons'])
            self.assertTrue(report['first_prediction_resource_readiness_checked'])
            self.assertFalse(report['future_resource_capacity_reserved'])
            self.assertFalse(report['worker_launch_authority'])
            self.assertFalse(report['trial_allocated'])
            self.assertEqual(json.loads((root / 'trainer-forward-admission.json').read_text()), report)

    def test_prediction_readiness_is_not_a_standalone_gpu_admission(self):
        gate = Gate.__new__(Gate)
        with self.assertRaisesRegex(EvidenceError, 'REQUIRES_FORWARD_REGISTRATION'):
            gate.check(prediction_ready=True)


if __name__ == '__main__': unittest.main()
