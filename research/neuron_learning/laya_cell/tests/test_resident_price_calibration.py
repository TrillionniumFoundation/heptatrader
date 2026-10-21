import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell import resident_price_baselines as baselines
from laya_cell import resident_price_calibration as calibration


def population():
    rows = []
    for i in range(32):
        anchor = 1000000+i*600000
        state = {'instrument': 'EUR.USD', 'anchor_ms': anchor,
                 'horizon_sec': 300, 'reference_mid': '1.12',
                 'columns': ['offset_ms', 'bid', 'ask'],
                 'quotes': [[at, '1.11999', '1.12001']
                            for at in (-300000, -240000, -180000, -120000, -60000, 0)]}
        rows.append({'sample_id': digest({'fixture': i}), 'anchor_ms': anchor,
                     'horizon_sec': 300, 'outcome_available_at_ms': anchor+300001,
                     'outcome_receipt_digest': digest({'observed_fixture': i}),
                     'state': state, 'target_class': 3 if i < 24 else 2 if i < 28 else 4})
    return rows


def real_dataset_population():
    """Use the public causal parser, including actual observed outcome times."""
    from laya_cell.dataset import rows_for_training
    from laya_cell.resident_training_v2 import timed_rows
    dataset = {'schema': 'heptatrader.neuron-dataset.v1', 'instrument': 'EUR.USD',
               'source_kind': 'REAL_PAPER_RUNTIME', 'scope': 'test-fixture', 'samples': []}
    for row in population():
        anchor = row['anchor_ms']
        inp = {'quotes': [{'t': anchor+q[0], 'bid': q[1], 'ask': q[2]}
                          for q in row['state']['quotes']]}
        target = {2: -.02, 3: 0., 4: .02}[row['target_class']]
        outcome = {'observed_at_ms': anchor+300000, 'available_at_ms': anchor+300001,
                   'target_scaled': target}
        dataset['samples'].append({'sample_id': digest(inp), 'anchor_ms': anchor,
            'reference_mid': '1.12', 'scope': dataset['scope'], 'known_at_ms': anchor,
            'input_snapshot': inp, 'input_gap': False,
            'label_reasons': ['OBSERVED', 'MISSING'], 'targets': [target, None],
            'outcome_receipts': [outcome, None]})
    window = {'training_end_ms': 20000000,
              'validation_start_ms': 21000000, 'validation_end_ms': 22000000}
    rows, _ = rows_for_training(dataset, window)
    return dataset, window, timed_rows(rows, dataset)


class PriceCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.rows = population()
        self.cutoff = 20000000
        self.reference = baselines.freeze(self.rows, self.cutoff)
        self.raw = [[.6, .1, .05, .05, .07, .08, .05] for _ in self.rows]

    def fit(self, **changes):
        kw = dict(cutoff_ms=self.cutoff, parent_id='a'*64,
                  adapter_sha256='b'*64, price_baselines=self.reference)
        kw.update(changes)
        return calibration.fit(self.rows, self.raw, **kw)

    def test_real_class_prior_mismatch_is_corrected_with_finite_unseen_mass(self):
        p = self.fit()
        result = calibration.training_measurement(p, self.raw, self.rows)
        self.assertLess(result['models']['calibrated_child']['mean_brier'],
                        result['models']['raw_child']['mean_brier'])
        q = calibration.apply(p, self.raw[0])
        self.assertEqual(max(range(7), key=q.__getitem__), 3)
        self.assertTrue(all(math.isfinite(v) and 0 < v < 1 for v in q))
        self.assertAlmostEqual(sum(q), 1.)
        self.assertEqual(p['real_target_class_counts'][0], 0)
        self.assertEqual(p['real_target_class_counts'][6], 0)
        self.assertEqual(p['training_rows'], 32)
        self.assertEqual(p['additional_independent_training_rows'], 0)
        self.assertFalse(p['future_value_proven'])

    def test_frozen_transform_keeps_input_dependence_and_does_not_refit(self):
        p = self.fit()
        first = calibration.apply(p, self.raw[0])
        second = calibration.apply(p, [.1, .1, .2, .3, .1, .1, .1])
        self.assertNotEqual(first, second)
        self.assertEqual(p, self.fit())
        self.assertGreaterEqual(p['logit_scale'], .25)
        self.assertLessEqual(p['logit_scale'], 1.)

    def test_other_population_or_cutoff_is_rejected(self):
        other = copy.deepcopy(self.reference)
        other['training_row_ids_digest'] = 'f'*64
        other['profile_id'] = digest({k: v for k, v in other.items() if k != 'profile_id'})
        with self.assertRaisesRegex(ContractError, 'POPULATION_CHANGED'):
            self.fit(price_baselines=other)
        with self.assertRaisesRegex(ContractError, 'POPULATION_CHANGED'):
            self.fit(cutoff_ms=self.cutoff+1)

    def test_noncausal_or_duplicate_rows_cannot_enter_fit(self):
        self.rows[0]['outcome_available_at_ms'] = self.cutoff+1
        with self.assertRaisesRegex(ContractError, 'NONCAUSAL'):
            self.fit()
        self.rows = population()
        self.rows[-1] = copy.deepcopy(self.rows[0])
        with self.assertRaisesRegex(ContractError, 'POPULATION_CHANGED'):
            self.fit()

    def test_parameter_tampering_and_rehashed_out_of_bounds_are_rejected(self):
        p = self.fit()
        changed = copy.deepcopy(p)
        changed['logit_scale'] += .001
        with self.assertRaisesRegex(ContractError, 'FROZEN_PROFILE'):
            calibration.apply(changed, self.raw[0])
        changed['logit_scale'] = 0
        changed['profile_id'] = digest({k: v for k, v in changed.items() if k != 'profile_id'})
        with self.assertRaisesRegex(ContractError, 'PARAMETERS'):
            calibration.apply(changed, self.raw[0])

    def test_nonfinite_or_invalid_raw_probabilities_rejected(self):
        p = self.fit()
        for values in ([float('nan')]+[1/6]*6, [True, 0, 0, 0, 0, 0, 0], [.1]*7):
            with self.assertRaises(ContractError):
                calibration.apply(p, values)

    def test_future_raw_mapping_is_rebuilt_and_tampering_rejected(self):
        p = self.fit()
        plan = {'parent_id': 'a'*64, 'price_output_profile': calibration.OUTPUT,
                'price_calibration_profile_id': p['profile_id'],
                'price_calibration': p, 'price_baselines': self.reference}
        prediction = {'parent_id': 'a'*64, 'trained_until_ms': self.cutoff,
                      'price_output_profile': calibration.OUTPUT,
                      'price_calibration_profile_id': p['profile_id'],
                      'candidate_raw_probs': self.raw[0],
                      'candidate_probs': calibration.apply(p, self.raw[0])}
        self.assertEqual(calibration.verify_prediction(plan, prediction), p)
        altered = copy.deepcopy(prediction)
        altered['candidate_probs'][0], altered['candidate_probs'][3] = (
            altered['candidate_probs'][3], altered['candidate_probs'][0])
        with self.assertRaisesRegex(ContractError, 'FORECAST_CHANGED'):
            calibration.verify_prediction(plan, altered)
        altered = copy.deepcopy(prediction)
        altered['trained_until_ms'] -= 1
        with self.assertRaisesRegex(ContractError, 'CONTEXT_CHANGED'):
            calibration.verify_prediction(plan, altered)
        self.assertIsNone(calibration.verify_prediction({}, {}))
        with self.assertRaisesRegex(ContractError, 'UNREGISTERED'):
            calibration.verify_prediction({}, prediction)

    def test_future_evaluator_scores_one_real_outcome_for_raw_calibrated_and_baselines(self):
        from laya_cell.resident_evaluate import measure
        from laya_cell.resident_training_v2 import ACTION_OUTPUT
        p = self.fit()
        state = copy.deepcopy(self.rows[0]['state'])
        state['anchor_ms'] = self.cutoff+60000
        anchor = state['anchor_ms']
        plan = {'candidate_id': 'c', 'parent_id': 'a'*64, 'plan_id': 'p',
                'rule': {'horizon_ms': 300000},
                'price_baselines': self.reference, 'price_value_rule': baselines.VALUE_RULE,
                'price_output_profile': calibration.OUTPUT,
                'price_calibration_profile_id': p['profile_id'], 'price_calibration': p,
                'action_output_profile': ACTION_OUTPUT,
                'action_training_profile_id': None, 'action_optimizer_admitted': False}
        prediction = {'candidate_id': 'c', 'parent_id': 'a'*64, 'plan_id': 'p', 'slot': 0,
            'anchor_ms': anchor, 'created_at_ms': anchor+1, 'trained_until_ms': self.cutoff,
            'horizon_sec': 300, 'input': state, 'input_id': digest(state), 'reference_mid': 1.12,
            'candidate_raw_probs': self.raw[0], 'candidate_probs': calibration.apply(p, self.raw[0]),
            'parent_probs': self.raw[0], 'price_output_profile': calibration.OUTPUT,
            'price_calibration_profile_id': p['profile_id'], 'candidate_act_escalate_probs': None,
            'bootstrap_point_bps': 0., 'action_output_profile': ACTION_OUTPUT,
            'action_inference': {'frozen_profile_id': None, 'action_correct_probs': None,
                                 'model_or_trade_permission': False},
            'price_baseline_forecast': baselines.forecast(self.reference, state)}
        quotes = [{'t': anchor+i*10000, 'mid': 1.12, 'receipt': str(i)} for i in range(31)]
        result = measure(plan, 0, prediction, quotes, anchor+300001)
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['target_class'], 3)
        self.assertEqual(set(result['same_window_price_scores']), set(baselines.MODELS))
        self.assertEqual(result['raw_child_price_scores'], baselines.probability_metrics(self.raw[0], 3))
        self.assertEqual(result['same_window_price_scores']['laya_candidate'],
                         baselines.probability_metrics(prediction['candidate_probs'], 3))
        self.assertNotEqual(result['raw_child_price_scores'], result['same_window_price_scores']['laya_candidate'])
        prediction['candidate_raw_probs'] = [.1, .1, .2, .3, .1, .1, .1]
        with self.assertRaisesRegex(ContractError, 'FORECAST_CHANGED'):
            measure(plan, 0, prediction, quotes, anchor+300001)


class CandidateFitReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dataset, self.window, self.rows = real_dataset_population()
        self.path = Path(self.tmp.name)/'dataset.json'
        self.path.write_text(json.dumps(self.dataset))
        self.job = {'dataset': str(self.path), 'dataset_sha256': sha_file(self.path),
                    'window': self.window, 'parent_id': 'a'*64,
                    'training_row_ids': [calibration.row_id(r) for r in self.rows]}
        raw = [[.6, .1, .05, .05, .07, .08, .05] for _ in self.rows]
        refs = baselines.freeze(self.rows, self.window['training_end_ms'])
        profile = calibration.fit(self.rows, raw, cutoff_ms=self.window['training_end_ms'],
            parent_id=self.job['parent_id'], adapter_sha256='b'*64, price_baselines=refs)
        self.candidate = {'adapter_sha256': 'b'*64, 'price_baselines': refs,
            'price_calibration': profile,
            'price_calibration_training_measurement': calibration.training_measurement(profile, raw, self.rows),
            'final_price_raw_forward_receipts': [
                {'training_row_id': calibration.row_id(row), 'raw_probs': probabilities,
                 **{key: row[key] for key in ('target_class', 'anchor_ms', 'outcome_available_at_ms')}}
                for row, probabilities in zip(self.rows, raw)]}

    def test_candidate_fit_replays_pinned_causal_dataset_and_actual_forward_receipts(self):
        self.assertEqual(len(self.rows), 32)
        self.assertEqual(calibration.verify_candidate_fit(self.job, self.candidate),
                         self.candidate['price_calibration'])

    def test_changed_dataset_raw_vector_row_provenance_and_report_are_rejected(self):
        altered = copy.deepcopy(self.candidate)
        altered['final_price_raw_forward_receipts'][0]['raw_probs'] = [.1, .1, .2, .3, .1, .1, .1]
        with self.assertRaisesRegex(ContractError, 'FIT_REPLAY_CHANGED'):
            calibration.verify_candidate_fit(self.job, altered)
        for key in ('training_row_id', 'target_class', 'anchor_ms', 'outcome_available_at_ms'):
            altered = copy.deepcopy(self.candidate)
            altered['final_price_raw_forward_receipts'][0][key] = 'wrong'
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, 'FORWARD_ROW_CHANGED'):
                calibration.verify_candidate_fit(self.job, altered)
        altered = copy.deepcopy(self.candidate)
        altered['price_calibration_training_measurement']['held_out'] = True
        with self.assertRaisesRegex(ContractError, 'TRAIN_MEASUREMENT_CHANGED'):
            calibration.verify_candidate_fit(self.job, altered)
        self.path.write_text(self.path.read_text()+' ')
        with self.assertRaisesRegex(ContractError, 'DATASET_CHANGED'):
            calibration.verify_candidate_fit(self.job, self.candidate)


if __name__ == '__main__':
    unittest.main()
