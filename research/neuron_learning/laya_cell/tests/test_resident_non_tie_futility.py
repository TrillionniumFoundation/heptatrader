"""Frozen sample impossibility releases work, never changes the trial verdict."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from laya_cell import promotion, resident_evaluate as evaluation, resident_queue
from laya_cell import promotion_power as power
from laya_cell import resident_parent_calibration as parent_calibration
from laya_cell.integrity import ContractError, digest, read_json, sha_file
from laya_cell.tests import test_evaluator_prospective_diagnostics as dispatch_fixture
from laya_cell.tests import test_matched_parent_screen as matched_fixture


class NonTieFutilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        matched_fixture.MatchedParentScreenTests.setUpClass()
        cls.matched = matched_fixture.MatchedParentScreenTests()

    def plan(self, protocol=power.PROTOCOL):
        plan = self.matched.plan(protocol)
        plan.pop('plan_id')
        plan['scope'] = 'test-period'
        plan['plan_id'] = digest(plan)
        return plan

    def results(self, plan, ties=0, censors=0, *, matched_score=0.8, raw_score=0.9):
        rows = self.matched.outcomes(plan, matched_score=matched_score, raw_score=raw_score)
        primary = ('calibrated_parent_brier' if plan['rule'].get('schema') == power.MATCHED_PARENT_SCHEMA
                   else 'parent_brier')
        for row in rows[:ties]:
            row[primary] = row['candidate_brier']
            row['same_window_price_scores']['frozen_parent']['brier'] = row['parent_brier']
        for row in rows[ties:ties+censors]:
            row['status'] = 'CENSORED'
            row['reason'] = 'ENDPOINT_GAP'
        for row in rows:
            row['evaluated_at_ms'] = row['anchor_ms'] + plan['rule']['horizon_ms']
        return rows

    def test_only_irreversible_loss_of_non_tie_capacity_releases_work(self):
        plan = self.plan()
        self.assertEqual(plan['rule']['slots'] - plan['rule']['min_non_ties'], 2)
        for ties, censors, expected in ((0, 0, False), (2, 0, False), (3, 0, True),
                                        (1, 1, False), (2, 1, True), (1, 2, True)):
            with self.subTest(ties=ties, censors=censors):
                rows = self.results(plan, ties, censors)[:ties+censors]
                original = copy.deepcopy(rows)
                self.assertIs(evaluation.suppress_new_work_for_frozen_failure(plan, rows), expected)
                self.assertEqual(rows, original)
        # Losing comparisons still contribute to the frozen non-tie sample.
        rows = self.results(plan, raw_score=0.2)[:5]
        self.assertFalse(evaluation.suppress_new_work_for_frozen_failure(plan, rows))

    def test_matched_parent_primary_is_used_instead_of_raw_parent_gain(self):
        plan = self.plan(power.MATCHED_PARENT_PROTOCOL)
        ties = self.results(plan, matched_score=0.4, raw_score=0.9)[:3]
        self.assertTrue(evaluation.suppress_new_work_for_frozen_failure(plan, ties))
        raw_ties = self.results(plan, matched_score=0.8, raw_score=0.4)[:3]
        self.assertFalse(evaluation.suppress_new_work_for_frozen_failure(plan, raw_ties))

    def test_unknown_scores_and_foreign_comparator_never_prove_a_tie(self):
        plan = self.plan()
        for key in ('candidate_brier', 'parent_brier'):
            for value in (None, True, float('nan'), float('inf'), -0.1, 2.1, '0.4'):
                rows = self.results(plan, ties=3)[:3]
                rows[0][key] = value
                with self.subTest(key=key, value=value):
                    self.assertFalse(evaluation.suppress_new_work_for_frozen_failure(plan, rows))
        plan = self.plan(power.MATCHED_PARENT_PROTOCOL)
        rows = self.results(plan, ties=3)[:3]
        rows[0]['parent_calibration_comparator_id'] = 'another-comparator'
        self.assertFalse(evaluation.suppress_new_work_for_frozen_failure(plan, rows))
        with self.assertRaisesRegex(ContractError, 'PARENT_CALIBRATION_OBSERVATION_MISSING_OR_CHANGED'):
            promotion.assess(plan, rows, plan['outcome_end_ms'])

    def test_tolerance_matches_the_existing_final_sign_test(self):
        plan = self.plan()
        rows = self.results(plan, ties=3)
        rows[0]['parent_brier'] += 0.5e-12
        rows[0]['same_window_price_scores']['frozen_parent']['brier'] = rows[0]['parent_brier']
        self.assertTrue(evaluation.suppress_new_work_for_frozen_failure(plan, rows[:3]))
        verdict = promotion.assess(plan, rows, plan['outcome_end_ms'])
        self.assertEqual(verdict['non_ties'], plan['rule']['slots'] - 3)
        rows[0]['parent_brier'] += 2e-12
        rows[0]['same_window_price_scores']['frozen_parent']['brier'] = rows[0]['parent_brier']
        self.assertFalse(evaluation.suppress_new_work_for_frozen_failure(plan, rows[:3]))
        verdict = promotion.assess(plan, rows, plan['outcome_end_ms'])
        self.assertEqual(verdict['non_ties'], plan['rule']['min_non_ties'])

    def test_incomplete_diagnostic_comparator_is_unknown_even_if_both_ids_are_missing(self):
        plan = self.plan(power.MATCHED_PARENT_PROTOCOL)
        rows = self.results(plan, ties=3)[:3]
        for row in rows:
            row.pop('parent_calibration_comparator_id')
        for comparator in ({}, None, {'comparator_id': ''}):
            with self.subTest(comparator=comparator):
                plan[parent_calibration.RECORD] = comparator
                budget = evaluation._non_tie_budget(plan, rows)
                self.assertEqual(budget['known_ties'], 0)
                self.assertEqual(budget['observed_without_valid_primary_scores'], 3)
                self.assertFalse(budget['impossible_from_recorded_outcomes'])

    def test_legacy_rule_does_not_gain_a_new_minimum_non_tie_gate(self):
        plan = self.plan(None)
        rows = self.results(plan, ties=plan['rule']['slots'])
        self.assertFalse(evaluation.suppress_new_work_for_frozen_failure(plan, rows))

    def test_original_deadline_alpha_and_final_sample_requirement_are_retained(self):
        plan = self.plan()
        original = copy.deepcopy(plan)
        rows = self.results(plan, ties=3)
        self.assertTrue(evaluation.suppress_new_work_for_frozen_failure(plan, rows[:3]))
        pending = promotion.assess(plan, rows[:3], plan['outcome_end_ms'] - 1)
        self.assertEqual(pending['state'], 'PENDING')
        self.assertNotIn('statistical_qualification', pending)
        # Even perfect remaining observations cannot repair the sample size.
        verdict = promotion.assess(plan, rows, plan['outcome_end_ms'])
        self.assertEqual(verdict['state'], 'REJECTED')
        self.assertIn('INSUFFICIENT_NON_TIE_POWER_SAMPLE', verdict['reasons'])
        self.assertEqual(verdict['alpha'], 0.05 / (plan['trial'] * (plan['trial'] + 1)))
        self.assertEqual(plan, original)

    def folder(self, plan, rows):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        folder = root / 'evaluation' / 'original'
        folder.mkdir(parents=True)
        path = folder / 'plan.json'
        path.write_text(json.dumps(plan))
        for row in rows:
            (folder / f"outcome-{row['slot']:03}.json").write_text(json.dumps(row))
        return path

    def test_queue_releases_capacity_without_rewriting_or_closing_original_trial(self):
        plan = self.plan()
        path = self.folder(plan, self.results(plan, ties=3)[:3])
        original = {p: p.read_bytes() for p in path.parent.iterdir()}
        queue = resident_queue.inspect({'evaluation_directory': str(path.parent.parent),
                                        'period_sha256': plan['scope']})
        self.assertEqual((queue['pending_original_trials'], queue['viable_pending_evaluations'],
                          queue['futile_pending_trials']), (1, 0, 1))
        self.assertEqual(queue['futile_plan_ids'], [plan['plan_id']])
        self.assertFalse(queue['original_trial_assessments_changed'])
        self.assertEqual({p: p.read_bytes() for p in path.parent.iterdir()}, original)

    def test_minimal_v2_receipt_binds_original_ties_and_mixed_censors(self):
        for censors in (0, 1):
            with self.subTest(censors=censors):
                plan = self.plan(power.MATCHED_PARENT_PROTOCOL)
                rows = self.results(plan, ties=3-censors, censors=censors)[:3]
                path = self.folder(plan, rows)
                original = {p: p.read_bytes() for p in path.parent.iterdir()}
                now = plan['start_ms'] + 3 * plan['rule']['slot_spacing_ms']
                binding = evaluation._futility_suppression_receipt(path, plan, 3, at_ms=now)
                receipt = path.parent / 'futility-suppression-003.json'
                raw = receipt.read_bytes()
                value = read_json(receipt)
                self.assertEqual(value['schema'], 'heptatrader.laya-futility-suppression.v2')
                self.assertEqual(value['reason'], 'INSUFFICIENT_POSSIBLE_NON_TIE_POWER_SAMPLE')
                self.assertEqual([entry['slot'] for entry in value['basis_outcomes']], [0, 1, 2])
                self.assertFalse(value['resource_admission_attempted_at_suppression'])
                self.assertEqual(binding, evaluation._futility_suppression_receipt(path, plan, 3))
                self.assertEqual(receipt.read_bytes(), raw)
                self.assertTrue(all(p.read_bytes() == data for p, data in original.items()))

    def test_receipt_cannot_use_later_or_unmeasured_ties(self):
        plan = self.plan()
        now = plan['start_ms'] + 3 * plan['rule']['slot_spacing_ms']
        for update in ({'evaluated_at_ms': None}, {'evaluated_at_ms': now + 1}, {'slot': 4}):
            rows = self.results(plan, ties=3)[:3]
            rows[0].update(update)
            path = self.folder(plan, rows)
            with self.subTest(update=update), self.assertRaisesRegex(
                    ContractError, 'FUTILITY_SUPPRESSION_BASIS_CHANGED'):
                evaluation._futility_suppression_receipt(path, plan, 3, at_ms=now)
            self.assertFalse((path.parent / 'futility-suppression-003.json').exists())

    def test_receipt_rejects_changed_score_even_with_recomputed_receipt_hashes(self):
        plan = self.plan(power.MATCHED_PARENT_PROTOCOL)
        rows = self.results(plan, ties=3)[:3]
        path = self.folder(plan, rows)
        now = plan['start_ms'] + 3 * plan['rule']['slot_spacing_ms']
        evaluation._futility_suppression_receipt(path, plan, 3, at_ms=now)
        source = path.parent / 'outcome-000.json'
        row = read_json(source)
        row['calibrated_parent_brier'] = 0.8
        source.write_text(json.dumps(row))
        receipt = path.parent / 'futility-suppression-003.json'
        value = read_json(receipt)
        value['basis_outcomes'][0]['sha256'] = sha_file(source)
        value.pop('receipt_id')
        value['receipt_id'] = digest(value)
        receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(ContractError, 'FUTILITY_SUPPRESSION_BASIS_CHANGED'):
            evaluation._futility_suppression_receipt(path, plan, 3)

    def test_suppression_generated_censor_cannot_complete_a_circular_proof(self):
        plan = self.plan()
        rows = self.results(plan, ties=2, censors=1)[:3]
        rows[2]['no_prediction_disposition'] = 'FUTILITY_SUPPRESSED'
        path = self.folder(plan, rows)
        now = plan['start_ms'] + 3 * plan['rule']['slot_spacing_ms']
        with self.assertRaisesRegex(ContractError, 'FUTILITY_SUPPRESSION_NOT_IRREVERSIBLE'):
            evaluation._futility_suppression_receipt(path, plan, 3, at_ms=now)
        self.assertFalse((path.parent / 'futility-suppression-003.json').exists())

    def test_coverage_reports_effective_sample_without_changing_assessment(self):
        plan = self.plan(power.MATCHED_PARENT_PROTOCOL)
        rows = self.results(plan, ties=2, censors=1)[:5]
        rows[-1].pop('candidate_brier')
        assessment = {'state': 'PENDING', 'reason': 'FIXED_FUTURE_WINDOW_NOT_COMPLETE'}
        original = copy.deepcopy(assessment)
        progress = evaluation.progress_with_coverage(plan, rows, assessment)
        budget = progress['coverage_diagnostics']['non_tie_budget']
        self.assertEqual((budget['known_non_ties'], budget['known_ties'],
                          budget['observed_without_valid_primary_scores']), (1, 2, 1))
        self.assertEqual(budget['primary_score'], 'calibrated_parent_brier')
        self.assertEqual(budget['maximum_possible_non_ties'], plan['rule']['slots'] - 3)
        self.assertTrue(budget['impossible_from_recorded_outcomes'])
        self.assertTrue(budget['unknown_scores_assumed_non_ties_for_upper_bound'])
        self.assertEqual(assessment, original)
        self.assertEqual({k: v for k, v in progress.items() if k != 'coverage_diagnostics'}, original)

    def test_evaluator_skips_gpu_and_records_only_actual_suppression_event(self):
        fixture = dispatch_fixture.ProspectiveEvaluatorDiagnosticsTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.plan['start_ms'] = fixture.now - 3 * fixture.plan['rule']['slot_spacing_ms']
        fixture.write_plan()
        for slot in range(3):
            fixture.outcome(slot, status='OBSERVED', parent_brier=0.4, candidate_brier=0.4)
        before = {p: p.read_bytes() for p in fixture.folder.glob('outcome-*.json')}
        engine = fixture.engine()
        status = fixture.step(engine)
        self.assertEqual(status['new_prediction_launches_suppressed'], 1)
        self.assertEqual(status['counts'], {'PENDING': 1})
        self.assertFalse((fixture.folder / 'qualification.json').exists())
        self.assertEqual(read_json(fixture.folder / 'futility-suppression-003.json')['schema'],
                         'heptatrader.laya-futility-suppression.v2')
        fixture.step(engine, fixture.now + fixture.plan['rule']['input_grace_ms'] + 1)
        row = read_json(fixture.folder / 'outcome-003.json')
        self.assertEqual(row['no_prediction_disposition'], 'FUTILITY_SUPPRESSED')
        self.assertTrue(all(p.read_bytes() == raw for p, raw in before.items()))


if __name__ == '__main__':
    unittest.main()
