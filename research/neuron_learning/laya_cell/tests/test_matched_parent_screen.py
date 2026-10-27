"""New forward screens isolate parameter gain without reclassifying old plans."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from laya_cell import promotion
from laya_cell import promotion_power as power
from laya_cell import resident_parent_calibration as comparison
from laya_cell import resident_price_baselines as baselines
from laya_cell import resident_price_input as inputs
from laya_cell import resident_training_v8 as policy
from laya_cell.integrity import ContractError, digest, read_json
from laya_cell.resident import Supervisor
from laya_cell.resident_row_accounting import row_id
from laya_cell.resident_store import database, get_meta, put_meta, write_once
from laya_cell.tests.test_resident_price_baselines import population


class MatchedParentScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows, cutoff = population()
        rows = [row for row in rows if row['horizon_sec'] == 300]
        for row in rows:
            row['state']['quantity'] = 'underlying MID return; not execution profit'
        cls.rows, cls.cutoff = rows, cutoff
        cls.parent = {'candidate_id': 'a' * 64, 'adapter_sha256': 'b' * 64}
        cls.baselines = baselines.freeze(rows, cutoff)
        job = {comparison.FIELD: comparison.PROFILE,
               'objective': copy.deepcopy(policy.OBJECTIVE),
               'parent_id': cls.parent['candidate_id'],
               'parent_manifest_sha256': 'd' * 64,
               'training_row_ids': [row_id(row) for row in rows],
               'window': {'training_end_ms': cutoff}}
        receipts = [{'training_row_id': row_id(row), 'raw_probs': [1 / 7] * 7,
                     'price_input_profile': inputs.RAW,
                     'price_input_id': digest(row['state'])} for row in rows]
        cls.comparator = comparison.freeze(job, rows, receipts, cls.parent, cls.baselines)

    def plan(self, protocol=power.MATCHED_PARENT_PROTOCOL, trial=55):
        rule = promotion.rule_for_trial(trial, protocol)
        start = self.cutoff + 120000
        plan = {'candidate_id': 'c' * 64, 'parent_id': self.parent['candidate_id'],
                'trial': trial, 'rule': rule, 'start_ms': start,
                'outcome_end_ms': start + rule['slots'] * rule['slot_spacing_ms'],
                'price_baselines': copy.deepcopy(self.baselines),
                'price_value_rule': copy.deepcopy(baselines.VALUE_RULE),
                comparison.FIELD: comparison.PROFILE,
                comparison.RECORD: copy.deepcopy(self.comparator),
                **inputs.bindings(self.parent, objective_schema=policy.SCHEMA)}
        plan['plan_id'] = digest(plan)
        return plan

    def outcomes(self, plan, *, matched_score=0.8, raw_score=0.9):
        result = []
        for slot in range(plan['rule']['slots']):
            anchor = plan['start_ms'] + slot * plan['rule']['slot_spacing_ms']
            scores = {name: {'brier': 0.8} for name in baselines.MODELS}
            scores['laya_candidate']['brier'] = 0.4
            scores['frozen_parent']['brier'] = raw_score
            result.append({'candidate_id': plan['candidate_id'], 'plan_id': plan['plan_id'],
                'slot': slot, 'status': 'OBSERVED', 'anchor_ms': anchor,
                'prediction_available_ms': anchor, 'trained_until_ms': self.cutoff,
                'path_gap': False, 'target_class': slot % 2, 'horizon_sec': 300,
                'candidate_brier': 0.4, 'parent_brier': raw_score,
                'calibrated_parent_brier': matched_score,
                'parent_calibration_comparator_id': self.comparator['comparator_id'],
                'candidate_hit': True, 'bootstrap_hit': False,
                'price_baseline_profile_id': self.baselines['profile_id'],
                'same_window_price_scores': scores})
        return result

    def assess(self, plan, outcomes):
        return promotion.assess(plan, outcomes, plan['outcome_end_ms'])

    def test_raw_parent_win_does_not_hide_no_gain_over_matched_parent(self):
        old = self.plan(power.PROTOCOL)
        new = self.plan()
        legacy = self.assess(old, self.outcomes(old, matched_score=0.3))
        screened = self.assess(new, self.outcomes(new, matched_score=0.3))
        self.assertEqual(legacy['state'], 'QUALIFIED_AWAITING_CANONICAL_OWNER')
        self.assertFalse(legacy['same_budget_calibrated_parent']['selection_gate'])
        self.assertEqual(screened['state'], 'REJECTED')
        self.assertIn('NO_POSITIVE_MEAN_IMPROVEMENT', screened['reasons'])
        self.assertIn('PAIRED_SIGN_EVIDENCE_INSUFFICIENT', screened['reasons'])
        self.assertTrue(screened['same_budget_calibrated_parent']['selection_gate'])
        self.assertEqual(screened['wins'], 0)

    def test_primary_sign_and_mean_use_matched_parent_with_no_trade_authority(self):
        plan = self.plan()
        report = self.assess(plan, self.outcomes(plan, raw_score=0.3))
        self.assertEqual(report['state'], 'QUALIFIED_AWAITING_CANONICAL_OWNER')
        self.assertEqual(report['primary_comparator'], power.MATCHED_PARENT)
        self.assertAlmostEqual(report['mean_brier_improvement'], 0.4)
        self.assertAlmostEqual(report['raw_parent_mean_brier_improvement'], -0.1)
        self.assertEqual(report['wins'], plan['rule']['slots'])
        for key in ('automatic_execution_selection', 'canonical_owner_admitted', 'promotion_performed'):
            self.assertIs(report[key], False)

    def test_simple_baseline_failure_still_rejects_a_matched_parent_win(self):
        plan = self.plan()
        outcomes = self.outcomes(plan)
        for row in outcomes:
            row['same_window_price_scores']['linear_softmax']['brier'] = 0.2
        report = self.assess(plan, outcomes)
        self.assertEqual(report['state'], 'REJECTED')
        self.assertIn('NO_POSITIVE_BRIER_IMPROVEMENT_VS_LINEAR_SOFTMAX', report['reasons'])

    def test_same_alpha_budget_power_and_missing_slot_rules_are_retained(self):
        plan = self.plan()
        old_rule = promotion.rule_for_trial(plan['trial'], power.PROTOCOL)
        for key in ('slots', 'min_observed', 'min_non_ties', 'max_censored',
                    'planned_critical_wins', 'planned_power', 'global_alpha_budget',
                    'alpha_spending', 'slot_spacing_ms', 'horizon_ms'):
            self.assertEqual(plan['rule'][key], old_rule[key])
        outcomes = self.outcomes(plan)
        for row in outcomes[-3:]:
            row['status'] = 'CENSORED'
        report = self.assess(plan, outcomes)
        self.assertEqual(report['alpha'], 0.05 / (55 * 56))
        self.assertIn('EXCESS_CENSORED_WINDOWS', report['reasons'])
        self.assertIn('INSUFFICIENT_OBSERVABLE_FUTURE_WINDOWS', report['reasons'])

    def test_full_scores_before_deadline_never_finish_trial(self):
        plan = self.plan()
        report = promotion.assess(plan, self.outcomes(plan), plan['outcome_end_ms'] - 1)
        self.assertEqual(report['state'], 'PENDING')
        self.assertNotIn('statistical_qualification', report)

    def test_missing_or_confounded_comparison_fails_before_qualification(self):
        for key in (comparison.FIELD, comparison.RECORD, 'price_value_rule'):
            plan = self.plan()
            plan.pop(key)
            with self.subTest(key=key), self.assertRaises(ContractError):
                promotion.validate_plan_rule(plan)
        plan = self.plan()
        plan['parent_price_input_profile'] = inputs.COMPACT
        with self.assertRaisesRegex(ContractError, 'INPUTS_NOT_ISOLATED'):
            promotion.validate_plan_rule(plan)
        plan = self.plan()
        plan['rule']['min_observed'] -= 1
        with self.assertRaisesRegex(ContractError, 'PROMOTION_RULE_CHANGED'):
            promotion.validate_plan_rule(plan)

    def test_comparator_binding_and_finite_score_cannot_be_replaced(self):
        for mutation in ('identity', 'score'):
            plan = self.plan()
            outcomes = self.outcomes(plan)
            if mutation == 'identity':
                outcomes[0]['parent_calibration_comparator_id'] = '0' * 64
            else:
                outcomes[0]['calibrated_parent_brier'] = float('nan')
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                    ContractError, 'PARENT_CALIBRATION_OBSERVATION_MISSING_OR_CHANGED'):
                self.assess(plan, outcomes)

    def test_new_config_does_not_upgrade_old_jobs_or_reset_trial_number(self):
        configured = {'promotion_protocol': power.MATCHED_PARENT_PROTOCOL}
        for original in (None, power.PROTOCOL, power.MATCHED_PARENT_PROTOCOL):
            job = {} if original is None else {'promotion_protocol': original}
            self.assertEqual(promotion.protocol_for_job(configured, job), original)
        self.assertEqual(promotion.protocol_for_job({'promotion_protocol': power.PROTOCOL},
            {'promotion_protocol': power.MATCHED_PARENT_PROTOCOL}), power.MATCHED_PARENT_PROTOCOL)
        self.assertGreaterEqual(promotion.rule_for_trial(55, power.MATCHED_PARENT_PROTOCOL)['min_non_ties'],
                                promotion.rule_for_trial(54, power.PROTOCOL)['min_non_ties'])

    def test_config_requires_v8_comparator_and_new_runtime_pins(self):
        config = {'promotion_protocol': power.MATCHED_PARENT_PROTOCOL,
                  'objective': copy.deepcopy(policy.OBJECTIVE),
                  comparison.FIELD: comparison.PROFILE,
                  'runtime_sha256': {name: 'b' * 64 for name in comparison.RUNTIME_PINS}}
        with self.assertRaisesRegex(ContractError, 'MATCHED_PARENT_SCREEN_RUNTIME_PINS_REQUIRED'):
            promotion.check_protocol_config(config)
        config['runtime_sha256']['learn/laya_cell/promotion_power.py'] = 'c' * 64
        promotion.check_protocol_config(config)
        config.pop(comparison.FIELD)
        with self.assertRaisesRegex(ContractError, 'MATCHED_PARENT_SCREEN_REQUIRES_V8_COMPARATOR'):
            promotion.check_protocol_config(config)

    def compatibility_registration(self, *, history=(1, 2), current=2, floor=2,
                                   selected=None):
        folder = tempfile.TemporaryDirectory(prefix='hepta-matched-registration-')
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        service = Supervisor.__new__(Supervisor)
        service.root = root
        service.db = database(root / 'scheduler.sqlite')
        self.addCleanup(service.db.close)
        service.c = {'evaluation_directory': str(root / 'evaluation'),
                     'period_sha256': 'a' * 64, 'promotion_protocol': selected,
                     'promotion_legacy_trial_floor': floor}
        if current is not None:
            with service.db:
                put_meta(service.db, 'evaluation_trial', current)
        for trial in history:
            plan = self.plan(trial=trial)
            plan.pop('plan_id')
            plan['scope'] = service.c['period_sha256']
            plan['plan_id'] = digest(plan)
            target = root / 'evaluation' / f'old-{trial}'
            write_once(target / 'plan.json', plan)
            write_once(target / 'qualification.json', {
                'candidate_id': plan['candidate_id'], 'plan_id': plan['plan_id'],
                'state': 'REJECTED'})
        job = {'promotion_protocol': power.MATCHED_PARENT_PROTOCOL,
               'promotion_legacy_trial_floor': floor}
        candidate = {'candidate_id': 'c' * 64}
        def build(job_id, candidate, job, trial, now):
            plan = self.plan(promotion.protocol_for_job(service.c, job), trial)
            plan.pop('plan_id')
            plan.update(job_id=job_id, scope=service.c['period_sha256'])
            return plan
        # Keep actual queue/history/SQLite allocation and immutable publication.
        # Artifact reading and the unrelated owner outbox are inert fixtures.
        service._evaluation_plan = Mock(side_effect=build)
        service._write_evaluation_handoff = Mock()
        return service, job, candidate

    def test_downgraded_config_cannot_reset_frozen_v3_trial_or_lose_history(self):
        for history, current, floor, reason in (
                ((55,), None, 55, 'PROMOTION_TRIAL_HISTORY_RESET_OR_CONFLICT'),
                ((), None, 55, 'PROMOTION_TRIAL_HISTORY_RESET_OR_CONFLICT'),
                ((1, 3), 3, 3, 'FORWARD_UNMATCHED_HISTORICAL_TRIAL_ALLOCATION')):
            with self.subTest(history=history, current=current):
                service, job, candidate = self.compatibility_registration(
                    history=history, current=current, floor=floor)
                service.recover()
                before = {path: path.read_bytes() for path in
                          Path(service.c['evaluation_directory']).glob('*/*.json')}
                with self.assertRaisesRegex(ContractError, reason):
                    service.register_evaluation('new', candidate, job)
                self.assertEqual(get_meta(service.db, 'evaluation_trial'), current)
                self.assertFalse((Path(service.c['evaluation_directory']) / 'new/plan.json').exists())
                self.assertEqual({path: path.read_bytes() for path in before}, before)
                service._evaluation_plan.assert_not_called()
                service._write_evaluation_handoff.assert_not_called()

    def test_downgraded_frozen_v3_job_keeps_unpublished_reservation_blocking(self):
        service, job, candidate = self.compatibility_registration()
        with service.db:
            put_meta(service.db, 'forward_plan_registration:old-reservation', {'state': 'RESERVED'})
        with self.assertRaisesRegex(ContractError, 'FORWARD_UNPUBLISHED_TRIAL_RESERVATION'):
            service.register_evaluation('new', candidate, job)
        self.assertEqual(get_meta(service.db, 'evaluation_trial'), 2)
        service._evaluation_plan.assert_not_called()

    def test_consistent_history_can_register_v3_after_downgrade_exactly_once(self):
        for selected in (None, power.PROTOCOL):
            with self.subTest(selected=selected):
                service, job, candidate = self.compatibility_registration(selected=selected)
                service.recover()
                before = {path: path.read_bytes() for path in
                          Path(service.c['evaluation_directory']).glob('*/*.json')}
                service.register_evaluation('new', candidate, job)
                path = Path(service.c['evaluation_directory']) / 'new/plan.json'
                plan = read_json(path)
                self.assertEqual(plan['trial'], 3)
                self.assertEqual(plan['rule'], promotion.rule_for_trial(3, power.MATCHED_PARENT_PROTOCOL))
                self.assertEqual(get_meta(service.db, 'evaluation_trial'), 3)
                service.register_evaluation('new', candidate, job)
                self.assertEqual(get_meta(service.db, 'evaluation_trial'), 3)
                self.assertEqual(read_json(path), plan)
                self.assertEqual({path: path.read_bytes() for path in before}, before)
                service._evaluation_plan.assert_called_once()
                service._write_evaluation_handoff.assert_called_once()


class MatchedParentAdmissionHistoryTests(unittest.TestCase):
    """Exercise actual admission with a real synthetic v8 candidate artifact."""

    def fixture(self, *, floor=55, selected=None):
        from laya_cell.tests.test_resident_training_v8 import RawCenteredIntegrationTests
        from laya_cell.tests import test_resident_training_v6_integration as source_fixture
        original = source_fixture.ProspectiveV6IntegrationTests.fixture
        def prepare_v3(case, *args, prepare_job=None, **kwargs):
            def prepare(job):
                if prepare_job is not None:
                    prepare_job(job)
                job['promotion_protocol'] = power.MATCHED_PARENT_PROTOCOL
                job['promotion_legacy_trial_floor'] = floor
            return original(case, *args, prepare_job=prepare, **kwargs)
        with patch.object(source_fixture.ProspectiveV6IntegrationTests, 'fixture', prepare_v3):
            service, job, candidate, _, _ = RawCenteredIntegrationTests.fixture(
                self, True, parent_comparator=True)
        service.c['promotion_protocol'] = selected
        service.c['runtime_sha256']['learn/laya_cell/promotion_power.py'] = 'b' * 64
        return service, job, candidate, source_fixture.source_fixture.CUTOFF + 2000

    def register(self, service, job, candidate, now):
        from laya_cell import resident_plan_admission as admission
        from laya_cell.tests.test_resident_plan_admission import fresh_proof
        with patch.object(admission, 'inspect_queue', return_value={'pending_original_trials': 0}), \
                patch.object(admission, 'capture_source', return_value=fresh_proof(now)):
            return admission.register(service, 'v6-job', candidate, job, clock=lambda: now)

    def test_admission_cannot_reuse_alpha_when_history_and_counter_rollback_together(self):
        from laya_cell import resident_plan_admission as admission
        for selected in (None, power.PROTOCOL, power.MATCHED_PARENT_PROTOCOL):
            with self.subTest(selected=selected):
                service, job, candidate, now = self.fixture(selected=selected)
                history = {p: p.read_bytes() for p in
                           Path(service.c['evaluation_directory']).glob('*/plan.json')}
                self.assertEqual(get_meta(service.db, 'evaluation_trial'), 1)
                with patch.object(admission, 'capture_source') as capture, \
                        self.assertRaisesRegex(ContractError, 'PROMOTION_TRIAL_HISTORY_RESET_OR_CONFLICT'):
                    admission.register(service, 'v6-job', candidate, job, clock=lambda: now)
                capture.assert_not_called()
                self.assertEqual(get_meta(service.db, 'evaluation_trial'), 1)
                self.assertIsNone(get_meta(service.db, admission.KEY_PREFIX + 'v6-job'))
                self.assertEqual({p: p.read_bytes() for p in history}, history)
                self.assertFalse((Path(service.c['evaluation_directory']) / 'v6-job/plan.json').exists())

    def test_admission_cannot_treat_lost_history_and_counter_as_a_new_campaign(self):
        from laya_cell import resident_plan_admission as admission
        service, job, candidate, now = self.fixture()
        for path in Path(service.c['evaluation_directory']).glob('*/plan.json'):
            path.unlink()
        with service.db:
            service.db.execute("DELETE FROM meta WHERE key='evaluation_trial'")
        with patch.object(admission, 'capture_source') as capture, \
                self.assertRaisesRegex(ContractError, 'PROMOTION_TRIAL_HISTORY_RESET_OR_CONFLICT'):
            admission.register(service, 'v6-job', candidate, job, clock=lambda: now)
        capture.assert_not_called()
        self.assertIsNone(get_meta(service.db, 'evaluation_trial'))
        self.assertIsNone(get_meta(service.db, admission.KEY_PREFIX + 'v6-job'))
        self.assertFalse((Path(service.c['evaluation_directory']) / 'v6-job/plan.json').exists())

    def test_consistent_admission_continues_frozen_v3_without_changing_old_trial(self):
        service, job, candidate, now = self.fixture(floor=1)
        history = {p: p.read_bytes() for p in
                   Path(service.c['evaluation_directory']).glob('*/plan.json')}
        result = self.register(service, job, candidate, now)
        self.assertEqual(result['state'], 'PUBLISHED')
        plan = read_json(Path(service.c['evaluation_directory']) / 'v6-job/plan.json')
        self.assertEqual(plan['trial'], 2)
        self.assertEqual(plan['rule'], promotion.rule_for_trial(2, power.MATCHED_PARENT_PROTOCOL))
        self.assertEqual(get_meta(service.db, 'evaluation_trial'), 2)
        self.assertEqual({p: p.read_bytes() for p in history}, history)

    def test_reserved_and_published_v3_recovery_do_not_make_a_new_admission_decision(self):
        from laya_cell import resident_plan_admission as admission
        service, job, candidate, now = self.fixture(floor=1)
        with patch.object(admission, '_publish', side_effect=OSError('synthetic crash')):
            with self.assertRaises(OSError):
                self.register(service, job, candidate, now)
        reserved = get_meta(service.db, admission.KEY_PREFIX + 'v6-job')
        self.assertEqual(reserved['state'], 'RESERVED')
        with patch.object(service, '_check_power_trial_history', side_effect=AssertionError('new admission')), \
                patch.object(admission, 'capture_source', side_effect=AssertionError('new source read')):
            result = admission.register(service, 'v6-job', candidate, job, clock=lambda: now + 86400000)
            self.assertEqual(result['state'], 'PUBLISHED')
            again = admission.register(service, 'v6-job', candidate, job, clock=lambda: now + 172800000)
        self.assertEqual(again, result)
        self.assertEqual(get_meta(service.db, 'evaluation_trial'), 2)
        self.assertEqual(read_json(Path(service.c['evaluation_directory']) / 'v6-job/plan.json'), reserved['plan'])


if __name__ == '__main__':
    unittest.main()
