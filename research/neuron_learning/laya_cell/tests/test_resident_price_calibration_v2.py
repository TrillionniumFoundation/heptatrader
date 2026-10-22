from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell import resident_price_baselines as baselines
from laya_cell import resident_price_calibration as legacy
from laya_cell import resident_price_calibration_v2 as calibration
from laya_cell.tests.test_resident_price_calibration import population, real_dataset_population


class BrierCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.rows = population()
        self.cutoff = 20000000
        self.reference = baselines.freeze(self.rows, self.cutoff)

    def fit(self, raw):
        return calibration.fit(self.rows, raw, cutoff_ms=self.cutoff,
            parent_id='a'*64, adapter_sha256='b'*64, price_baselines=self.reference)

    def test_exact_prior_fallback_and_degenerate_direction(self):
        raw = [[1., 0., 0., 0., 0., 0., 0.] for _ in self.rows]
        profile = self.fit(raw)
        self.assertEqual(profile['model_weight'], 0.)
        for probabilities in (raw[0], [1/7]*7, [0., 0., 0., 1., 0., 0., 0.]):
            self.assertEqual(calibration.apply(profile, probabilities),
                             self.reference['train_prior_by_horizon']['300']['probs'])
        flat = [profile['train_prior_probs'][:] for _ in self.rows]
        degenerate = self.fit(flat)
        self.assertEqual(degenerate['model_weight'], 0.)
        report = calibration.training_measurement(profile, raw, self.rows)
        self.assertFalse(report['held_out'])
        self.assertFalse(report['future_value_proven'])
        self.assertTrue(report['exact_prior_fallback'])
        self.assertEqual(report['models']['calibrated_child']['mean_brier'],
                         report['models']['train_prior']['mean_brier'])

    def test_perfect_raw_reaches_endpoint_without_probability_floor(self):
        raw = [[float(k == r['target_class']) for k in range(7)] for r in self.rows]
        p = self.fit(raw)
        self.assertEqual(p['model_weight'], 1.)
        self.assertEqual(calibration.apply(p, raw[0]), raw[0])
        self.assertEqual(calibration.training_measurement(p, raw, self.rows)
                         ['models']['calibrated_child']['mean_brier'], 0.)

    def test_interior_solution_minimizes_original_brier_and_beats_both_endpoints(self):
        raw = [[float(k == (r['target_class'] if i % 2 == 0 else 0)) for k in range(7)]
               for i, r in enumerate(self.rows)]
        p = self.fit(raw)
        self.assertGreater(p['model_weight'], 0.)
        self.assertLess(p['model_weight'], 1.)
        def score(weight):
            return sum(sum(((1-weight)*a+weight*b-float(k == r['target_class']))**2
                           for k, (a, b) in enumerate(zip(p['train_prior_probs'], values)))
                       for r, values in zip(self.rows, raw))/len(self.rows)
        best = score(p['model_weight'])
        self.assertLess(best, score(0.))
        self.assertLess(best, score(1.))
        for i in range(101):
            self.assertLessEqual(best, score(i/100)+1e-14)
        self.assertAlmostEqual(score(p['model_weight']-.00001),
                               score(p['model_weight']+.00001), places=14)

    def test_frozen_transform_binding_and_rehashed_parameter_tampering(self):
        raw = [[1/7]*7 for _ in self.rows]
        p = self.fit(raw)
        for key, value in [('model_weight', .5), ('train_prior_probs', [1/7]*7),
                            ('quadratic_denominator', -1.), ('future_value_proven', True)]:
            changed = deepcopy(p)
            changed[key] = value
            changed['profile_id'] = digest({k: v for k, v in changed.items() if k != 'profile_id'})
            with self.subTest(key=key), self.assertRaises(ContractError):
                calibration.validate(changed)
        for probabilities in ([True, 0, 0, 0, 0, 0, 0], [float('nan')]+[1/6]*6, [.1]*7):
            with self.assertRaises(ContractError):
                calibration.apply(p, probabilities)
        self.rows[0]['outcome_available_at_ms'] = self.cutoff+1
        with self.assertRaisesRegex(ContractError, 'NONCAUSAL'):
            self.fit(raw)

    def test_future_prediction_uses_frozen_coefficient_and_original_baseline_identity(self):
        raw = [[1/7]*7 for _ in self.rows]
        p = self.fit(raw)
        plan = {'parent_id': 'a'*64, 'price_output_profile': calibration.OUTPUT,
                'price_calibration_profile_id': p['profile_id'],
                'price_calibration': p, 'price_baselines': self.reference}
        prediction = {'parent_id': 'a'*64, 'trained_until_ms': self.cutoff,
                      'price_output_profile': calibration.OUTPUT,
                      'price_calibration_profile_id': p['profile_id'],
                      'candidate_raw_probs': raw[0], 'candidate_probs': calibration.apply(p, raw[0])}
        self.assertEqual(legacy.verify_prediction(plan, prediction), p)
        changed = deepcopy(prediction)
        changed['candidate_probs'] = [1/7]*7
        with self.assertRaisesRegex(ContractError, 'FORECAST_CHANGED'):
            calibration.verify_prediction(plan, changed)
        changed = deepcopy(plan)
        changed['price_output_profile'] = legacy.OUTPUT
        with self.assertRaisesRegex(ContractError, 'CONTEXT_CHANGED'):
            legacy.verify_prediction(changed, prediction)

    def test_rebuild_real_dataset_and_final_child_receipts_without_gpu(self):
        dataset, window, rows = real_dataset_population()
        raw = [[1/7]*7 for _ in rows]
        reference = baselines.freeze(rows, window['training_end_ms'])
        p = calibration.fit(rows, raw, cutoff_ms=window['training_end_ms'],
            parent_id='a'*64, adapter_sha256='b'*64, price_baselines=reference)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'dataset.json'
            path.write_text(json.dumps(dataset))
            job = {'dataset': str(path), 'dataset_sha256': sha_file(path), 'window': window,
                   'training_row_ids': [legacy.row_id(r) for r in rows], 'parent_id': 'a'*64}
            candidate = {'price_output_profile': calibration.OUTPUT,
                         'adapter_sha256': 'b'*64, 'price_baselines': reference,
                         'price_calibration': p,
                         'price_calibration_training_measurement': calibration.training_measurement(p, raw, rows),
                         'final_price_raw_forward_receipts': [
                             {'training_row_id': legacy.row_id(r), 'raw_probs': values,
                              **{k: r[k] for k in ('target_class', 'anchor_ms', 'outcome_available_at_ms')}}
                             for r, values in zip(rows, raw)]}
            self.assertEqual(calibration.verify_candidate_fit(job, candidate), p)
            candidate['final_price_raw_forward_receipts'][0]['target_class'] = 0
            with self.assertRaisesRegex(ContractError, 'FORWARD_ROW_CHANGED'):
                calibration.verify_candidate_fit(job, candidate)

    def test_scheduler_import_does_not_load_torch(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            'import sys; from laya_cell import resident_price_calibration_v2; '
            'assert "torch" not in sys.modules'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
