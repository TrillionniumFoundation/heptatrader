"""Observational coverage must not change frozen qualification or provenance."""
import copy
import json
import unittest
from unittest.mock import patch

from laya_cell import resident_evaluate
from laya_cell.promotion import RULE, assess, rule_for_trial
from laya_cell.promotion_power import PROTOCOL


class CoverageDiagnosticsTests(unittest.TestCase):
    def plan(self, trial=1, protocol=None):
        rule = rule_for_trial(trial, protocol)
        return {'candidate_id': 'candidate', 'plan_id': 'plan', 'trial': trial,
                'rule': rule, 'outcome_end_ms': rule['slots'] * 360000 + 300000}

    def outcome(self, slot, status='CENSORED', **fields):
        return {'candidate_id': 'candidate', 'plan_id': 'plan',
                'slot': slot, 'status': status, **fields}

    def observed(self, slot):
        return self.outcome(slot, 'OBSERVED', anchor_ms=slot * 360000,
                            prediction_available_ms=slot * 360000,
                            trained_until_ms=-1, path_gap=False,
                            target_class=slot % 2, parent_brier=0.75,
                            candidate_brier=0.5, candidate_hit=True,
                            bootstrap_hit=False)

    def progress(self, plan, results, assessment):
        assessment_bytes = json.dumps(assessment, sort_keys=True).encode()
        original_results = copy.deepcopy(results)
        with patch.object(resident_evaluate, 'read_json',
                          side_effect=AssertionError('diagnostics must reuse results')), \
                patch.object(resident_evaluate, 'sha_file',
                             side_effect=AssertionError('diagnostics must not read receipts')):
            progress = resident_evaluate.progress_with_coverage(plan, results, assessment)
        self.assertEqual(json.dumps(assessment, sort_keys=True).encode(), assessment_bytes)
        self.assertEqual(results, original_results)
        self.assertIsNot(progress, assessment)
        self.assertEqual({key: value for key, value in progress.items()
                          if key != 'coverage_diagnostics'}, assessment)
        return progress

    def test_recorded_categories_denials_and_failure_phases_keep_distinct_counts(self):
        plan = self.plan(50, PROTOCOL)
        results = [self.observed(0),
                   self.outcome(1, reason='NO_PREDICTION_IN_FIXED_SLOT',
                                no_prediction_disposition='ADMISSION_DENIAL_OBSERVED',
                                first_admission_denial_reasons=['RESOURCE_HOLD', 'RESOURCE_HOLD', 'NO_WINDOW']),
                   self.outcome(2, reason='NO_PREDICTION_IN_FIXED_SLOT',
                                no_prediction_disposition='FUTILITY_SUPPRESSED',
                                first_admission_denial_reasons=['RESOURCE_HOLD']),
                   self.outcome(3, reason='PREDICTION_PROCESS_FAILED',
                                detail='WORKER_SIGNAL_SIGSEGV', phase={'phase': 'STARTUP', 'at_ms': 1}),
                   self.outcome(4, reason='PREDICTION_PROCESS_FAILED',
                                detail='WORKER_BUDGET_EXCEEDED', phase='PREDICTION'),
                   self.outcome(5, reason='PATH_GAP')]
        assessment = assess(plan, results, plan['outcome_end_ms'] - 1)
        coverage = self.progress(plan, results, assessment)['coverage_diagnostics']
        self.assertTrue(coverage['observational_only'])
        self.assertEqual(coverage['frozen_rule'], {key: plan['rule'][key]
                         for key in ('slots', 'min_observed', 'min_non_ties', 'max_censored')})
        self.assertEqual(coverage['counts'], {'OBSERVED': 1, 'CENSORED': 5,
                                             'pending': plan['rule']['slots'] - 6})
        self.assertEqual(coverage['censor_budget']['remaining'], 0)
        self.assertEqual(coverage['censor_budget']['excess'], 3)
        self.assertEqual(coverage['censor_budget']['maximum_possible_observed'], plan['rule']['slots'] - 5)
        self.assertEqual(coverage['censored_reasons'], {'NO_PREDICTION_IN_FIXED_SLOT': 2,
                         'PREDICTION_PROCESS_FAILED': 2, 'PATH_GAP': 1})
        self.assertEqual(coverage['no_prediction_dispositions'],
                         {'ADMISSION_DENIAL_OBSERVED': 1, 'FUTILITY_SUPPRESSED': 1})
        self.assertEqual(coverage['first_admission_denial_reasons'], {'RESOURCE_HOLD': 2, 'NO_WINDOW': 1})
        self.assertEqual(coverage['first_admission_denial_slots'], 2)
        self.assertEqual(coverage['prediction_process_failed'],
                         {'details': {'WORKER_SIGNAL_SIGSEGV': 1, 'WORKER_BUDGET_EXCEEDED': 1},
                          'phases': {'STARTUP': 1, 'PREDICTION': 1},
                          'detail_unrecorded': 0, 'phase_unrecorded': 0})

    def test_censor_budget_boundary_does_not_turn_pending_trial_into_qualification(self):
        plan = self.plan()
        for censored, remaining, excess in ((0, 2, 0), (2, 0, 0), (3, 0, 1)):
            with self.subTest(censored=censored):
                results = [self.outcome(slot, reason='OUTCOME_MISSING') for slot in range(censored)]
                assessment = assess(plan, results, plan['outcome_end_ms'] - 1)
                progress = self.progress(plan, results, assessment)
                self.assertEqual(progress['state'], 'PENDING')
                self.assertNotIn('statistical_qualification', progress)
                self.assertEqual(progress['coverage_diagnostics']['censor_budget']['remaining'], remaining)
                self.assertEqual(progress['coverage_diagnostics']['censor_budget']['excess'], excess)

    def test_full_observation_still_waits_for_deadline_and_preserves_terminal_assessment(self):
        plan = self.plan()
        results = [self.observed(slot) for slot in range(RULE['slots'])]
        before = assess(plan, results, plan['outcome_end_ms'] - 1)
        pending = self.progress(plan, results, before)
        self.assertEqual(pending['state'], 'PENDING')
        self.assertEqual(pending['coverage_diagnostics']['counts']['pending'], 0)
        self.assertIsNone(pending['coverage_diagnostics']['frozen_rule']['min_non_ties'])
        after = assess(plan, results, plan['outcome_end_ms'])
        terminal = self.progress(plan, results, after)
        self.assertEqual(terminal['state'], 'QUALIFIED_AWAITING_CANONICAL_OWNER')
        self.assertTrue(terminal['statistical_qualification'])
        self.assertFalse(terminal['canonical_owner_admitted'])
        self.assertFalse(terminal['promotion_performed'])

    def test_missing_terminal_slots_keep_original_assessment_denominator_without_invented_reason(self):
        plan = self.plan()
        results = [self.observed(slot) for slot in range(RULE['min_observed'])]
        assessment = assess(plan, results, plan['outcome_end_ms'])
        progress = self.progress(plan, results, assessment)
        self.assertEqual(progress['state'], 'REJECTED')
        self.assertEqual(progress['censored'], 2)
        self.assertIn('MISSING_EVALUATION_SLOTS', progress['reasons'])
        coverage = progress['coverage_diagnostics']
        self.assertEqual(coverage['counts'], {'OBSERVED': 10, 'CENSORED': 0, 'pending': 2})
        self.assertEqual(coverage['censored_reasons'], {})
        self.assertEqual(coverage['no_prediction_dispositions'], {})
        self.assertEqual(coverage['first_admission_denial_reasons'], {})
        self.assertEqual(coverage['prediction_process_failed']['phases'], {})

    def test_unknown_history_is_counted_verbatim_without_filling_absent_provenance(self):
        plan = self.plan()
        results = [self.outcome(0, reason='HISTORICAL_UNCLASSIFIED_MISS'),
                   self.outcome(1, reason='NO_PREDICTION_IN_FIXED_SLOT'),
                   self.outcome(2), self.outcome(3, reason='PREDICTION_PROCESS_FAILED')]
        assessment = assess(plan, results, plan['outcome_end_ms'] - 1)
        coverage = self.progress(plan, results, assessment)['coverage_diagnostics']
        self.assertEqual(coverage['censored_reasons'], {'HISTORICAL_UNCLASSIFIED_MISS': 1,
                         'NO_PREDICTION_IN_FIXED_SLOT': 1, 'PREDICTION_PROCESS_FAILED': 1})
        self.assertEqual(coverage['censored_reason_unrecorded'], 1)
        self.assertEqual(coverage['no_prediction_disposition_unrecorded'], 1)
        self.assertEqual(coverage['no_prediction_dispositions'], {})
        self.assertEqual(coverage['first_admission_denial_reasons'], {})
        self.assertEqual(coverage['first_admission_denial_slots'], 0)
        self.assertEqual(coverage['prediction_process_failed'],
                         {'details': {}, 'phases': {}, 'detail_unrecorded': 1, 'phase_unrecorded': 1})
        self.assertEqual(coverage['counts']['pending'], RULE['slots'] - len(results))


if __name__ == '__main__':
    unittest.main()
