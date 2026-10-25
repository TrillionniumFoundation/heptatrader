"""CPU mathematics and immutable context for centered train-only calibration."""
from copy import deepcopy
import math
import random
import subprocess
import sys
import unittest

from laya_cell.integrity import ContractError, digest
from laya_cell import resident_price_baselines as baselines
from laya_cell import resident_price_calibration_v3 as calibration
from laya_cell import resident_parent_calibration as comparator
from laya_cell import resident_price_input as inputs
from laya_cell.resident_row_accounting import row_id
from laya_cell.tests.test_resident_price_calibration import population,real_dataset_population


class CenteredBrierCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.rows = population()
        self.cutoff = 20000000
        self.reference = baselines.freeze(self.rows, self.cutoff)

    def fit(self, raw, **changes):
        options = dict(cutoff_ms=self.cutoff, parent_id='a'*64,
                       adapter_sha256='b'*64, price_baselines=self.reference)
        options.update(changes)
        return calibration.fit(self.rows, raw, **options)

    def weak_signal(self, strength=.001):
        # The fixed alternating direction balances within each even-sized
        # label group; only the small centered label direction has covariance.
        empirical = [sum(r['target_class'] == k for r in self.rows)/len(self.rows)
                     for k in range(7)]
        return [[1/7 + .1*(-1 if i % 2 else 1)*((k == 0)-(k == 1))
                 + strength*((r['target_class'] == k)-empirical[k]) for k in range(7)]
                for i, r in enumerate(self.rows)]

    def score(self, values):
        return sum(sum((p-(k == row['target_class']))**2 for k, p in enumerate(vector))
                   for row, vector in zip(self.rows, values))/len(self.rows)

    def test_positive_conditional_direction_has_the_closed_form_interior_minimum(self):
        raw = self.weak_signal()
        profile = self.fit(raw)
        self.assertGreater(profile['quadratic_numerator'], 0.)
        self.assertGreater(profile['model_weight'], 0.)
        self.assertLess(profile['model_weight'], profile['model_weight_max'])
        self.assertEqual(profile['model_weight'], profile['quadratic_numerator']/profile['quadratic_denominator'])
        prior_score = self.score([profile['train_prior_probs']]*len(raw))
        actual = self.score([calibration.apply(profile, row) for row in raw])
        self.assertLess(actual, prior_score)
        normalized = [calibration._probabilities(row) for row in raw]
        def at(weight):
            return self.score([[p+weight*(v-m) for p,v,m in zip(
                profile['train_prior_probs'], row, profile['mean_training_raw_probs'])]
                for row in normalized])
        for i in range(101):
            self.assertLessEqual(actual, at(profile['model_weight_max']*i/100)+1e-14)
        self.assertAlmostEqual(at(profile['model_weight']-1e-5),
                               at(profile['model_weight']+1e-5), places=14)

    def test_negative_covariance_returns_the_exact_prior_for_every_future_vector(self):
        profile = self.fit(self.weak_signal(-.001))
        self.assertLess(profile['quadratic_numerator'], 0.)
        self.assertEqual(profile['model_weight'], 0.)
        for k in range(7):
            vertex = [float(i == k) for i in range(7)]
            self.assertEqual(calibration.apply(profile, vertex), profile['train_prior_probs'])

    def test_constant_probabilities_have_no_signal_even_with_decimal_mean_roundoff(self):
        for vector in ([.6, .1, .05, .05, .07, .08, .05], [1/7]*7,
                       [1., 0., 0., 0., 0., 0., 0.]):
            with self.subTest(vector=vector):
                raw = [vector[:] for _ in self.rows]
                profile = self.fit(raw)
                self.assertEqual(profile['mean_training_raw_probs'], calibration._probabilities(vector))
                self.assertEqual(profile['quadratic_numerator'], 0.)
                self.assertEqual(profile['quadratic_denominator'], 0.)
                self.assertEqual(profile['model_weight'], 0.)
                self.assertEqual(self.score([calibration.apply(profile, row) for row in raw]),
                                 self.score([profile['train_prior_probs']]*len(raw)))

    def test_constant_labels_with_varying_probabilities_have_exactly_zero_covariance(self):
        for row in self.rows:
            row['target_class'] = 3
        self.reference = baselines.freeze(self.rows, self.cutoff)
        generator = random.Random(0)
        raw = []
        for _ in self.rows:
            values = [generator.random() for _ in range(7)]
            raw.append([value/sum(values) for value in values])
        profile = self.fit(raw)
        self.assertGreater(profile['quadratic_denominator'], 0.)
        self.assertEqual(profile['quadratic_numerator'], 0.)
        self.assertEqual(profile['model_weight'], 0.)
        self.assertEqual(calibration.apply(profile, raw[0]), profile['train_prior_probs'])

    def test_strong_direction_is_bounded_and_all_future_simplex_vertices_are_valid(self):
        raw = [[.8*v+.2*(k == r['target_class']) for k,v in
                enumerate([.6, .1, .05, .05, .07, .08, .05])] for r in self.rows]
        profile = self.fit(raw)
        self.assertEqual(profile['model_weight'], profile['model_weight_max'])
        expected = min([1.]+[p/m for p,m in
                            zip(profile['train_prior_probs'], profile['mean_training_raw_probs']) if m > 0])
        self.assertLessEqual(profile['model_weight_max'], expected)
        self.assertTrue(math.isclose(profile['model_weight_max'], expected, rel_tol=1e-14))
        for k in range(7):
            values = calibration.apply(profile, [float(i == k) for i in range(7)])
            self.assertTrue(all(0 <= v <= 1 and math.isfinite(v) for v in values))
            self.assertAlmostEqual(sum(values), 1., places=15)

    def test_training_output_mean_is_prior_without_constant_sharpening(self):
        raw = self.weak_signal()
        profile = self.fit(raw)
        transformed = [calibration.apply(profile, row) for row in raw]
        for k in range(7):
            self.assertAlmostEqual(sum(row[k] for row in transformed)/len(transformed),
                                   profile['train_prior_probs'][k], places=14)
        self.assertFalse(profile['future_value_proven'])
        self.assertTrue(profile['calibration_is_train_only'])
        self.assertEqual(profile['additional_independent_training_rows'], 0)

    def test_unseen_classes_and_zero_training_means_need_no_floor_or_division_by_zero(self):
        raw = [[float(k == r['target_class']) for k in range(7)] for r in self.rows]
        profile = self.fit(raw)
        self.assertEqual(profile['mean_training_raw_probs'][0], 0.)
        self.assertGreater(profile['train_prior_probs'][0], 0.)
        for k in range(7):
            result = calibration.apply(profile, [float(i == k) for i in range(7)])
            self.assertTrue(all(0 <= p <= 1 for p in result))
            self.assertAlmostEqual(sum(result), 1., places=15)

    def test_rehashed_parameters_and_malformed_probabilities_are_rejected(self):
        profile = self.fit(self.weak_signal())
        changes = [('model_weight', profile['model_weight']/2),
                   ('model_weight_max', 1.), ('mean_training_raw_probs', [.1]*7),
                   ('train_prior_probs', [1/7]*7), ('quadratic_denominator', -1.),
                   ('future_value_proven', True), ('parent_id', 'wrong')]
        for key,value in changes:
            changed = deepcopy(profile)
            changed[key] = value
            changed['profile_id'] = digest({k:v for k,v in changed.items() if k != 'profile_id'})
            with self.subTest(key=key), self.assertRaises(ContractError):
                calibration.validate(changed)
        for key in ('model_weight', 'model_weight_max', 'quadratic_numerator', 'quadratic_denominator'):
            changed = deepcopy(profile)
            changed[key] = float('nan')
            with self.subTest(nan=key), self.assertRaises(ContractError):
                calibration.validate(changed)
        for raw in ([True, 0, 0, 0, 0, 0, 0], [float('nan')]+[1/6]*6,
                    [-.01, .2, .2, .2, .2, .2, .01], [.1]*7, [1.]*8):
            with self.subTest(raw=raw), self.assertRaises(ContractError):
                calibration.apply(profile, raw)

    def test_causal_row_and_population_binding_remain_required(self):
        raw = self.weak_signal()
        with self.assertRaisesRegex(ContractError, 'POPULATION_CHANGED'):
            self.fit(raw, cutoff_ms=self.cutoff+1)
        with self.assertRaisesRegex(ContractError, 'CONTEXT'):
            self.fit(raw, parent_id='invalid')
        self.rows[0]['outcome_available_at_ms'] = self.cutoff+1
        with self.assertRaisesRegex(ContractError, 'NONCAUSAL'):
            self.fit(raw)

    def test_training_measurement_keeps_raw_prior_and_conditional_gain_separate(self):
        raw = self.weak_signal()
        profile = self.fit(raw)
        report = calibration.training_measurement(profile, raw, self.rows)
        self.assertEqual(report['rows'], 32)
        self.assertTrue(report['training_only'])
        self.assertFalse(report['held_out'])
        self.assertFalse(report['future_value_proven'])
        self.assertEqual(report['additional_independent_rows'], 0)
        self.assertEqual(report['model_weight'], profile['model_weight'])
        self.assertFalse(report['exact_prior_fallback'])
        self.assertEqual(set(report['models']), {'raw_child', 'calibrated_child', 'train_prior', 'uniform'})
        self.assertAlmostEqual(report['models']['raw_child']['mean_brier'], self.score(raw), places=14)
        self.assertAlmostEqual(report['models']['calibrated_child']['mean_brier'],
                               self.score([calibration.apply(profile, row) for row in raw]), places=14)
        self.assertLess(report['models']['calibrated_child']['mean_brier'],
                        report['models']['train_prior']['mean_brier'])
        constant = [[.6, .1, .05, .05, .07, .08, .05] for _ in self.rows]
        fallback = self.fit(constant)
        report = calibration.training_measurement(fallback, constant, self.rows)
        self.assertTrue(report['exact_prior_fallback'])
        self.assertEqual(report['models']['calibrated_child'], report['models']['train_prior'])

    def test_scheduler_import_does_not_load_torch(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            'import sys; from laya_cell import resident_price_calibration_v3; '
            'assert "torch" not in sys.modules'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def comparator_fixture(self):
        _,window,self.rows=real_dataset_population()
        self.cutoff=window['training_end_ms']
        self.reference=baselines.freeze(self.rows,self.cutoff)
        raw=self.weak_signal()
        parent={'candidate_id':'a'*64,'adapter_sha256':'b'*64}
        job={comparator.FIELD:comparator.PROFILE,'objective':{'schema':inputs.V8_OBJECTIVE_SCHEMA},
             'parent_id':parent['candidate_id'],'parent_manifest_sha256':'c'*64,
             'window':{'training_end_ms':self.cutoff},'training_row_ids':[row_id(r) for r in self.rows]}
        receipts=[{'training_row_id':row_id(row),'raw_probs':probs,
                   'price_input_profile':inputs.RAW,'price_input_id':digest(inputs.model_state(row['state'],inputs.RAW))}
                  for row,probs in zip(self.rows,raw)]
        value=comparator.freeze(job,self.rows,receipts,parent,self.reference)
        candidate={comparator.FIELD:comparator.PROFILE,comparator.RECORD:value,
                   'price_baselines':self.reference,'parent_price_forward_receipts':receipts}
        return job,parent,candidate,raw

    def test_same_calibration_budget_removes_gain_from_applying_calibration_only_to_child(self):
        job,parent,candidate,raw=self.comparator_fixture()
        value=candidate[comparator.RECORD]
        child=self.fit(raw);frozen_parent=value['calibration']
        for vector in raw+[[float(i==k) for i in range(7)] for k in range(7)]:
            self.assertEqual(calibration.apply(child,vector),calibration.apply(frozen_parent,vector))
        self.assertNotEqual(child['profile_id'],frozen_parent['profile_id'])
        self.assertEqual(frozen_parent['raw_logits_scope'],calibration.PARENT_SCOPE)
        self.assertEqual(frozen_parent['fit_spec'],calibration.PARENT_SPEC)
        self.assertEqual(comparator.verify_candidate(job,candidate,rows=self.rows,parent=parent),value)
        plan={comparator.FIELD:comparator.PROFILE,comparator.RECORD:value,
              'parent_id':parent['candidate_id'],'price_baselines':self.reference}
        prediction={'parent_probs':raw[0],**comparator.prediction_fields(plan,raw[0])}
        outcome=comparator.measure(plan,prediction,self.rows[0]['target_class'])
        outcome['candidate_brier']=outcome['calibrated_parent_brier']
        report=comparator.assessment(plan,[outcome])
        self.assertEqual(report['mean_brier_improvement'],0.)
        self.assertEqual(report['paired_brier']['ties'],1)
        self.assertFalse(report['selection_gate'])

    def test_parent_comparator_cannot_be_added_to_old_job_or_substituted_as_child_output(self):
        job,parent,candidate,_=self.comparator_fixture()
        legacy={k:v for k,v in job.items() if k!=comparator.FIELD}
        with self.assertRaisesRegex(ContractError,'UNDECLARED_PARENT_CALIBRATION'):
            comparator.verify_candidate(legacy,candidate,rows=self.rows,parent=parent)
        self.assertEqual(comparator.plan_binding(legacy,{}),{})
        changed=deepcopy(candidate)
        changed[comparator.RECORD]['calibration']['adapter_sha256']='d'*64
        profile=changed[comparator.RECORD]['calibration']
        profile['profile_id']=digest({k:v for k,v in profile.items() if k!='profile_id'})
        value=changed[comparator.RECORD]
        value['comparator_id']=digest({k:v for k,v in value.items() if k!='comparator_id'})
        with self.assertRaisesRegex(ContractError,'PARENT_CALIBRATION_REPLAY_CHANGED'):
            comparator.verify_candidate(job,changed,rows=self.rows,parent=parent)
        changed_job=deepcopy(job);changed_job['training_row_ids'][0]='e'*64
        with self.assertRaisesRegex(ContractError,'PARENT_CALIBRATION_TRAINING_BINDING'):
            comparator.verify_candidate(changed_job,candidate,rows=self.rows,parent=parent)
        with self.assertRaisesRegex(ContractError,'CHILD_PRICE_CALIBRATION_SCOPE_CHANGED'):
            calibration.verify_prediction({'price_output_profile':calibration.OUTPUT,
                'price_input_objective':inputs.V8_OBJECTIVE_SCHEMA,
                'price_calibration':candidate[comparator.RECORD]['calibration']},{})
        with self.assertRaisesRegex(ContractError,'RUNTIME_PINS_REQUIRED'):
            comparator.check_config(job)
        job['runtime_sha256']={name:'f'*64 for name in comparator.RUNTIME_PINS}
        self.assertTrue(comparator.check_config(job))


if __name__ == '__main__':
    unittest.main()
