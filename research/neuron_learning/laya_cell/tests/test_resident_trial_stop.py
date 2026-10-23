"""Explicit operational abort preserves the frozen original CPU assessment."""
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from laya_cell.integrity import digest, sha_file
from laya_cell.resident_evaluate import Evaluator
from laya_cell.resident_retained_closeout import run_retained
from laya_cell import resident_queue as queue
from laya_cell import resident_trial_stop as stop
from laya_cell.tests import test_retained_futile_closeout as retained_fixture


class OperatorStopTests(unittest.TestCase):
    def setUp(self):
        self.old = retained_fixture.RetainedCloseoutTests()
        self.old.setUp()
        self.addCleanup(self.old.doCleanups)
        o = self.old
        for p in o.folder.glob('outcome-*.json'):
            p.unlink()
        o.plan['start_ms'] = o.now - 1000
        self.reseal_plan()
        runtime = Path(o.current['runtime_directory'])
        validator = runtime / stop.RUNTIME_PIN[0]
        validator.parent.mkdir(parents=True)
        shutil.copy2(Path(stop.__file__), validator)
        o.current.update(operator_trial_stop_profile=stop.PROFILE,
            operator_trial_stop_replacement_source_sha='e' * 40)
        o.current['runtime_sha256'] = dict(o.current['runtime_sha256'],
            **{stop.RUNTIME_PIN[0]: sha_file(validator)})
        self.receipt_path = o.folder / 'operator-stop.json'

    def reseal_plan(self):
        o = self.old
        o.plan['outcome_end_ms'] = (o.plan['start_ms'] +
            (o.plan['rule']['slots'] - 1) * o.plan['rule']['slot_spacing_ms'] +
            o.plan['rule']['input_grace_ms'] + o.plan['rule']['horizon_ms'] + 5000)
        o.plan.pop('plan_id', None)
        o.plan['plan_id'] = digest(o.plan)
        o.path.write_text(json.dumps(o.plan))
        o.current['retained_futile_plan_closeouts'][0]['plan_id'] = o.plan['plan_id']

    def write_stop(self):
        o = self.old
        receipt = stop.build_receipt(o.path, requested_at_ms=o.now,
            user_reason='Explicit operator switch; no performance conclusion or alpha refund.',
            replacement_source_sha='e' * 40)
        self.receipt_path.write_text(json.dumps(receipt))
        self.receipt_path.chmod(0o600)
        return stop.read_stop(o.current, o.path, o.plan)

    def execute(self, binding=None, cache=None):
        o = self.old
        return run_retained(o.current, o.path, o.plan, {} if cache is None else cache,
                            o.now, operator_stop=binding)

    def test_no_receipt_never_opens_viable_retained_runtime(self):
        o = self.old
        self.assertIsNone(stop.read_stop(o.current, o.path, o.plan))
        with self.assertRaisesRegex(ValueError, 'REQUIRES_IRREVERSIBLE_FUTILITY'):
            self.execute()
        self.assertEqual(list(o.folder.glob('outcome-*.json')), [])
        self.assertFalse((o.folder / 'qualification.json').exists())

    def test_valid_stop_preserves_plan_and_future_slots_without_gpu_or_alpha_refund(self):
        o = self.old
        before = o.path.read_bytes()
        binding = self.write_stop()
        result = self.execute(binding)
        self.assertEqual(result['schema'], 'fixture.original-evaluator.v1')
        self.assertEqual(result['counts'], {'PENDING': 1})
        self.assertEqual(result['operator_stopped_trials'], 1)
        self.assertTrue(result['operator_stop_non_performance_assessment'])
        self.assertFalse(result['new_heavy_workers_allowed'])
        self.assertFalse(result['operator_stop_torch_imported'])
        self.assertGreater(result['operator_stop_launches_suppressed'], 0)
        self.assertEqual(result['active_prediction_workers'], 0)
        self.assertEqual(list(o.folder.glob('prediction-*.json')), [])
        self.assertEqual(list(o.folder.glob('outcome-*.json')), [])
        self.assertFalse((o.folder / 'qualification.json').exists())
        self.assertEqual(o.path.read_bytes(), before)
        self.assertFalse(binding['receipt']['performance_assessment'])
        self.assertFalse(binding['receipt']['alpha_refunded'])
        self.assertEqual(list(o.root.rglob('*.sqlite')), [])

    def test_new_evaluator_routes_stop_before_replacement_source_check(self):
        o = self.old
        binding = self.write_stop()
        path = o.root / 'new.json'
        path.write_text(json.dumps(o.current))
        service = Evaluator(path)
        with patch.object(service, 'launch', side_effect=AssertionError('GPU forbidden')):
            result = service.step()
        self.assertEqual(result['counts'], {'PENDING': 1})
        self.assertEqual(result['operator_stopped_trials'], 1)
        self.assertEqual(result['operator_stopped_plans'][0]['receipt_sha256'], binding['sha256'])
        self.assertFalse(result['operator_stopped_plans'][0]['performance_assessment'])
        self.assertFalse((o.folder / 'qualification.json').exists())

    def test_queue_excludes_only_verified_stop_without_refunding_or_dropping_pending(self):
        o = self.old
        self.assertEqual(queue.inspect(o.current)['viable_pending_evaluations'], 1)
        self.write_stop()
        report = queue.inspect(o.current)
        self.assertEqual(report['viable_pending_evaluations'], 0)
        self.assertEqual(report['pending_original_trials'], 1)
        self.assertEqual(report['operator_stopped_trials'], 1)
        serial = queue.registration_policy(report, o.current, {}, o.now)
        self.assertEqual(serial['blocking_trials'], 1)
        enrolled = dict(o.current, future_trial_overlap_profile=queue.OVERLAP_PROFILE,
            future_trial_overlap_effective_at_ms=o.now - 1,
            future_plan_admission_profile='REGULAR_FX_FULL_WINDOW_ORIGINAL_SOURCE_V1')
        decision = queue.registration_policy(report, enrolled, enrolled, o.now)
        self.assertEqual(decision['blocking_trials'], 0)
        self.assertEqual(decision['bypassed_operator_stopped_plan_ids'], [o.plan['plan_id']])
        self.assertFalse(decision['alpha_debits_refunded'])

    def test_wrong_receipt_identity_or_flag_even_when_rehashed_is_rejected(self):
        self.write_stop()
        original = json.loads(self.receipt_path.read_text())
        for key, value in [('plan_id', 'a' * 64), ('candidate_id', 'foreign'),
                ('plan_sha256', 'b' * 64), ('period_sha256', 'c' * 64),
                ('replacement_source_sha', 'f' * 40), ('performance_assessment', True),
                ('alpha_refunded', True), ('new_heavy_workers_allowed', True),
                ('requested_at_ms', self.old.now + 86400000), ('profile', 'UNKNOWN')]:
            changed = dict(original, **{key: value})
            changed['receipt_id'] = digest({k: v for k, v in changed.items() if k != 'receipt_id'})
            self.receipt_path.write_text(json.dumps(changed))
            with self.subTest(field=key), self.assertRaises(ValueError):
                stop.read_stop(self.old.current, self.old.path, self.old.plan)
        self.assertEqual(list(self.old.folder.glob('outcome-*.json')), [])

    def test_missing_optin_wrong_pin_and_nonprivate_receipt_fail_closed(self):
        o = self.old
        self.write_stop()
        missing = dict(o.current)
        missing.pop('operator_trial_stop_profile')
        with self.assertRaisesRegex(ValueError, 'CONFIG_PROFILE'):
            stop.read_stop(missing, o.path, o.plan)
        unknown = dict(o.current, operator_trial_stop_profile='UNKNOWN')
        with self.assertRaisesRegex(ValueError, 'CONFIG_PROFILE'):
            stop.check_config(unknown)
        bad = dict(o.current, runtime_sha256={})
        with self.assertRaisesRegex(ValueError, 'RUNTIME_PIN'):
            stop.read_stop(bad, o.path, o.plan)
        self.receipt_path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'FILE_IDENTITY'):
            stop.read_stop(o.current, o.path, o.plan)
        stop.check_config({})

    def test_mutated_receipt_cannot_reuse_cached_closeout(self):
        o = self.old
        binding = self.write_stop()
        cache = {}
        self.execute(binding, cache)
        changed = json.loads(self.receipt_path.read_text())
        changed['user_reason'] = 'Different authorization snapshot'
        changed['receipt_id'] = digest({k: v for k, v in changed.items() if k != 'receipt_id'})
        self.receipt_path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'RECEIPT_CHANGED'):
            self.execute(binding, cache)

    def test_committed_future_prediction_is_not_early_censored_or_discarded(self):
        o = self.old
        anchor = o.plan['start_ms']
        prediction = {'candidate_id': o.plan['candidate_id'], 'plan_id': o.plan['plan_id'],
            'slot': 0, 'anchor_ms': anchor, 'input': {}, 'input_id': digest({}),
            'created_at_ms': anchor + 1, 'trained_until_ms': anchor - 1,
            'reference_mid': 1.12, 'candidate_probs': [1/7] * 7,
            'parent_probs': [1/7] * 7, 'candidate_act_escalate_probs': [.5, .5],
            'bootstrap_point_bps': 0.}
        path = o.folder / 'prediction-000.json'
        path.write_text(json.dumps(prediction))
        raw = path.read_bytes()
        result = self.execute(self.write_stop())
        self.assertEqual(result['counts'], {'PENDING': 1})
        self.assertEqual((result['observed'], result['censored']), (0, 0))
        self.assertEqual(path.read_bytes(), raw)
        self.assertFalse((o.folder / 'outcome-000.json').exists())

    def test_original_deadline_and_closed_qualification_remain_unchanged(self):
        o = self.old
        o.plan['start_ms'] -= 2 * 86400000
        self.reseal_plan()
        binding = self.write_stop()
        plan_before = o.path.read_bytes()
        result = self.execute(binding)
        self.assertEqual(result['counts'], {'REJECTED': 1})
        qpath = o.folder / 'qualification.json'
        qualification = qpath.read_bytes()
        self.assertEqual(json.loads(qualification)['plan_id'], o.plan['plan_id'])
        self.assertEqual(o.path.read_bytes(), plan_before)
        path = o.root / 'new.json'
        path.write_text(json.dumps(o.current))
        with patch('laya_cell.resident_retained_closeout.run_retained',
                   side_effect=AssertionError('closed assessment must win')):
            self.assertEqual(Evaluator(path).step()['counts'], {'REJECTED': 1})
        self.assertEqual(qpath.read_bytes(), qualification)

    def test_committed_prediction_is_scored_by_original_runtime_without_new_predictions(self):
        o = self.old
        o.plan['start_ms'] = o.now - 301000
        # Original source transport is an explicit synthetic fixture; scoring
        # code and its distinctive old-version schema are the real old copy.
        o.old_code.write_text(o.old_code.read_text() + '\n' +
            'def read_quotes(config, now_ms):\n return __import__("json").loads(' +
            repr(json.dumps([{'t': o.plan['start_ms'] + i * 10000,
                              'mid': 1.12, 'receipt': str(i)} for i in range(31)])) + '), []\n')
        o.plan['evaluation_runtime_sha256']['learn/laya_cell/resident_evaluate.py'] = sha_file(o.old_code)
        self.reseal_plan()
        old = json.loads(o.old_config.read_text())
        old['runtime_sha256']['learn/laya_cell/resident_evaluate.py'] = sha_file(o.old_code)
        o.old_config.write_text(json.dumps(old))
        o.current['retained_futile_plan_closeouts'][0]['config_sha256'] = sha_file(o.old_config)
        anchor = o.plan['start_ms']
        state = {'anchor_ms': anchor}
        prediction = {'candidate_id': o.plan['candidate_id'], 'plan_id': o.plan['plan_id'],
            'slot': 0, 'input': state, 'input_id': digest(state), 'anchor_ms': anchor,
            'created_at_ms': anchor + 1, 'trained_until_ms': anchor - 1,
            'reference_mid': 1.12, 'candidate_probs': [1/7] * 7,
            'parent_probs': [1/7] * 7, 'candidate_act_escalate_probs': [.5, .5],
            'bootstrap_point_bps': 0.}
        predpath = o.folder / 'prediction-000.json'
        predpath.write_text(json.dumps(prediction))
        before = predpath.read_bytes()
        result = self.execute(self.write_stop())
        self.assertEqual(result['schema'], 'fixture.original-evaluator.v1')
        self.assertEqual(result['observed'], 1)
        self.assertEqual(result['censored'], 0)
        outcome = json.loads((o.folder / 'outcome-000.json').read_text())
        self.assertEqual(outcome['status'], 'OBSERVED')
        self.assertEqual(outcome['prediction_file_sha256'], sha_file(predpath))
        self.assertEqual(predpath.read_bytes(), before)
        self.assertEqual(len(list(o.folder.glob('prediction-*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
