"""Prospective scheduling evidence; no workers, model loads or live records."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from laya_cell import resident_evaluate as evaluation
from laya_cell.integrity import ContractError, digest, read_json, sha_file
from laya_cell.promotion import rule_for_trial
from laya_cell.promotion_power import PROTOCOL


class ProspectiveEvaluatorDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.pinned = self.runtime / 'pinned.py'
        self.pinned.write_text('# original source\n')
        self.period = self.root / 'period.json'
        self.period.write_text('{"execution_domain":"PAPER"}')
        self.folder = self.root / 'evaluation' / 'old'
        self.folder.mkdir(parents=True)
        self.path = self.folder / 'plan.json'
        self.now = 1792790000000
        rule = rule_for_trial(39, PROTOCOL)
        self.plan = {'schema': 'heptatrader.laya-resident-future-plan.v1',
            'candidate_id': 'old-candidate', 'trial': 39, 'rule': rule,
            'start_ms': self.now, 'created_at_ms': self.now - 120000,
            'scope': sha_file(self.period), 'no_refit': True,
            'evaluation_runtime_sha256': {'pinned.py': sha_file(self.pinned)}}
        self.write_plan()
        self.config = {'evaluation_directory': str(self.folder.parent),
            'period_path': str(self.period), 'period_sha256': sha_file(self.period),
            'runtime_directory': str(self.runtime), 'promotion_protocol': PROTOCOL,
            'runtime_sha256': dict(self.plan['evaluation_runtime_sha256'])}

    def write_plan(self):
        rule = self.plan['rule']
        self.plan['outcome_end_ms'] = (self.plan['start_ms']
            + (rule['slots'] - 1) * rule['slot_spacing_ms']
            + rule['input_grace_ms'] + rule['horizon_ms'] + 5000)
        self.plan.pop('plan_id', None)
        self.plan['plan_id'] = digest(self.plan)
        self.path.write_text(json.dumps(self.plan))

    def outcome(self, slot, status='CENSORED', **extra):
        path = self.folder / f'outcome-{slot:03}.json'
        path.write_text(json.dumps({'candidate_id': self.plan['candidate_id'],
            'plan_id': self.plan['plan_id'], 'slot': slot, 'status': status,
            'reason': 'ORIGINAL_REASON', 'evaluated_at_ms': self.now - 1000, **extra}))
        return path

    def make_futile(self):
        self.plan['start_ms'] = self.now - 3 * self.plan['rule']['slot_spacing_ms']
        self.write_plan()
        for slot in range(3):
            self.outcome(slot)

    def engine(self):
        engine = object.__new__(evaluation.Evaluator)
        engine.path = self.root / 'config.json'
        engine.path.write_text(json.dumps(self.config))
        engine.c = dict(self.config)
        engine.root = self.folder.parent
        engine.tasks = {}
        engine.last_log_ms = self.now
        return engine

    def step(self, engine, now=None, launch=None):
        if now is None:
            now = self.now
        if launch is None:
            launch = Mock(side_effect=AssertionError('Worker launch forbidden'))
        with patch.object(evaluation.time, 'time', return_value=now / 1000), \
                patch.object(engine, 'launch', launch), \
                patch.object(evaluation, 'read_quotes', side_effect=AssertionError('No source scan')), \
                contextlib.redirect_stdout(io.StringIO()):
            return engine.step()

    def files(self):
        return {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}

    def test_late_worker_failure_preserves_prior_resource_refusal(self):
        engine = self.engine()
        when = self.now - evaluation.PREDICTION_PREWARM_MS
        with patch.object(evaluation.time, 'time', return_value=when / 1000):
            engine.record_admission_deferral(self.path, 0, ['HOST_FULL_MEMORY_PSI_PRESSURE'])
        denial = self.folder / 'admission-denial-000.json'
        original_denial = denial.read_bytes()
        phase = {'phase': 'WAIT_FOR_FIXED_SLOT_INPUT',
            'model_preparation_ms': 7951, 'input_budget_remaining_after_model_ms': -5554}
        failure = self.folder / 'failure-000.json'
        failure.write_text(json.dumps({'reason': 'INPUT_SLOT_EXPIRED:MODEL_LOAD_OR_SCHEDULING_LATE',
            'phase': phase}))
        raw = failure.read_bytes()
        self.step(engine, self.now + self.plan['rule']['input_grace_ms'] + 9000)
        result = read_json(self.folder / 'outcome-000.json')
        self.assertEqual(result['reason'], 'PREDICTION_PROCESS_FAILED')
        self.assertEqual(result['phase'], phase)
        self.assertEqual(result['first_admission_denial_reasons'], ['HOST_FULL_MEMORY_PSI_PRESSURE'])
        self.assertEqual(result['first_admission_denial_sha256'], sha_file(denial))
        self.assertEqual(result['first_admission_denial_at_ms'], when)
        self.assertTrue(result['not_proof_of_every_denial_attempt'])
        self.assertEqual(denial.read_bytes(), original_denial)
        self.assertEqual(failure.read_bytes(), raw)
        coverage = read_json(self.folder / 'progress.json')['coverage_diagnostics']
        self.assertEqual(coverage['counts']['CENSORED'], 1)
        self.assertEqual(coverage['first_admission_denial_slots'], 1)
        self.assertEqual(coverage['censored_reasons'], {'PREDICTION_PROCESS_FAILED': 1})

    def test_admitted_success_preserves_prior_refusal_without_changing_score(self):
        from laya_cell.promotion import assess
        engine = self.engine()
        with patch.object(evaluation.time, 'time', return_value=(self.now - 1000) / 1000):
            engine.record_admission_deferral(self.path, 0, ['HOST_FULL_MEMORY_PSI_PRESSURE'])
        prediction = self.folder / 'prediction-000.json'
        prediction.write_text(json.dumps({'anchor_ms': self.now}))
        measured = {'candidate_id': self.plan['candidate_id'], 'plan_id': self.plan['plan_id'],
            'slot': 0, 'status': 'OBSERVED', 'anchor_ms': self.now,
            'prediction_available_ms': self.now, 'trained_until_ms': self.now - 1,
            'path_gap': False, 'target_class': 0, 'parent_brier': 0.75,
            'candidate_brier': 0.5, 'candidate_hit': True, 'bootstrap_hit': False}
        now = self.now + self.plan['rule']['horizon_ms']
        with patch.object(evaluation.time, 'time', return_value=now / 1000), \
                patch.object(evaluation, 'read_quotes', return_value=([], [])), \
                patch.object(evaluation, 'measure', return_value=dict(measured)), \
                patch.object(engine, 'launch', return_value=False), \
                contextlib.redirect_stdout(io.StringIO()):
            engine.step()
        result = read_json(self.folder / 'outcome-000.json')
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertEqual(result['first_admission_denial_reasons'], ['HOST_FULL_MEMORY_PSI_PRESSURE'])
        self.assertEqual(assess(self.plan, [result], now), assess(self.plan, [measured], now))
        coverage = read_json(self.folder / 'progress.json')['coverage_diagnostics']
        self.assertEqual(coverage['counts']['OBSERVED'], 1)
        self.assertEqual(coverage['counts']['CENSORED'], 0)
        self.assertEqual(coverage['first_admission_denial_slots'], 1)

    def test_wrong_denial_identity_cannot_be_attached_to_worker_failure(self):
        engine = self.engine()
        with patch.object(evaluation.time, 'time', return_value=self.now / 1000):
            engine.record_admission_deferral(self.path, 0, ['RESOURCE_HOLD'])
        denial = self.folder / 'admission-denial-000.json'
        value = read_json(denial); value['plan_id'] = 'another-plan'
        denial.write_text(json.dumps(value))
        (self.folder / 'failure-000.json').write_text(json.dumps({
            'reason': 'WORKER_SIGNAL_SIGSEGV', 'phase': {'phase': 'VALIDATE_AND_LOAD'}}))
        with self.assertRaisesRegex(ContractError, 'PREWARM_DENIAL_OUTCOME_IDENTITY'):
            self.step(engine)
        self.assertFalse((self.folder / 'outcome-000.json').exists())

    def test_terminal_status_keeps_source_and_worker_failures_distinct_without_rereading_outcomes(self):
        from laya_cell.promotion import assess
        end=self.plan['outcome_end_ms'];statuses=[];qualification_bytes=None
        for reason,detail in (('PATH_GAP',None),('PREDICTION_PROCESS_FAILED','WORKER_SIGNAL_SIGSEGV')):
            results=[{'candidate_id':self.plan['candidate_id'],'plan_id':self.plan['plan_id'],
                'slot':slot,'status':'CENSORED','reason':reason,
                **({'detail':detail,'phase':{'phase':'PREDICTION'}} if detail else {})}
                for slot in range(self.plan['rule']['slots'])]
            assessment=assess(self.plan,results,end)
            raw=json.dumps(assessment,sort_keys=True).encode()
            if qualification_bytes is not None:self.assertEqual(raw,qualification_bytes)
            qualification_bytes=raw
            qualification=self.folder/'qualification.json';qualification.write_bytes(raw)
            progress=evaluation.progress_with_coverage(self.plan,results,assessment)
            (self.folder/'progress.json').write_text(json.dumps(progress))
            original_read=evaluation.read_json
            def no_outcomes(path,*args,**kwargs):
                self.assertFalse(Path(path).name.startswith('outcome-'))
                return original_read(path,*args,**kwargs)
            with patch.object(evaluation,'read_json',side_effect=no_outcomes):
                statuses.append(self.step(self.engine(),end+1))
            self.assertEqual(qualification.read_bytes(),qualification_bytes)
        source=statuses[0]['closed_coverage_diagnostics'];worker=statuses[1]['closed_coverage_diagnostics']
        self.assertNotEqual(source,worker)
        self.assertEqual(source['censored_reasons'],{'PATH_GAP':self.plan['rule']['slots']})
        self.assertEqual(worker['prediction_process_failed']['details'],
            {'WORKER_SIGNAL_SIGSEGV':self.plan['rule']['slots']})
        self.assertTrue(source['observational_only']);self.assertFalse(source['model_performance_result'])
        self.assertEqual(source['authorization_effect'],'NONE')

    def test_terminal_status_never_uses_old_pending_progress_or_future_deadline(self):
        from laya_cell.promotion import assess
        end=self.plan['outcome_end_ms'];assessment=assess(self.plan,[],end)
        qualification=self.folder/'qualification.json';qualification.write_text(json.dumps(assessment))
        pending=assess(self.plan,[],end-1)
        (self.folder/'progress.json').write_text(json.dumps(evaluation.progress_with_coverage(self.plan,[],pending)))
        result=self.step(self.engine(),end+1)['closed_coverage_diagnostics']
        self.assertEqual(result['eligible_closed_plans'],1)
        self.assertEqual(result['plans_with_diagnostics'],0)
        self.assertEqual(result['plans_without_diagnostics'],1)
        original_read=evaluation.read_json
        def no_progress(path,*args,**kwargs):
            self.assertNotEqual(Path(path).name,'progress.json')
            return original_read(path,*args,**kwargs)
        with patch.object(evaluation,'read_json',side_effect=no_progress):
            result=self.step(self.engine(),end-1)['closed_coverage_diagnostics']
        self.assertEqual(result['eligible_closed_plans'],0)

    def test_natural_closeout_reports_existing_coverage_then_caches_final_progress(self):
        end=self.plan['outcome_end_ms']
        for slot in range(self.plan['rule']['slots']):self.outcome(slot,reason='ENDPOINT_GAP')
        engine=self.engine();result=self.step(engine,end+1)
        summary=result['closed_coverage_diagnostics']
        self.assertEqual(summary['plans_with_diagnostics'],1)
        self.assertEqual(summary['censored_reasons'],{'ENDPOINT_GAP':self.plan['rule']['slots']})
        qualification=self.folder/'qualification.json';original=qualification.read_bytes()
        reads=[];original_read=evaluation.read_json
        def no_outcomes(path,*args,**kwargs):
            self.assertFalse(Path(path).name.startswith('outcome-'))
            if Path(path).name=='progress.json':reads.append(path)
            return original_read(path,*args,**kwargs)
        with patch.object(evaluation,'read_json',side_effect=no_outcomes):
            self.assertEqual(self.step(engine,end+2)['closed_coverage_diagnostics'],summary)
            self.assertEqual(self.step(engine,end+3)['closed_coverage_diagnostics'],summary)
        self.assertEqual(len(reads),1)
        self.assertEqual(qualification.read_bytes(),original)

    def retained_other_route(self):
        self.config['retained_futile_plan_closeouts'] = [{
            'plan_id': 'unrelated-old-plan', 'config_path': str(self.root / 'unused.json'),
            'config_sha256': 'a' * 64}]

    def test_unrouted_old_source_holds_without_starving_current_plan(self):
        self.outcome(0, status='OBSERVED')
        before = self.files()
        self.pinned.unlink()
        current_source = self.runtime / 'current.py'
        current_source.write_text('# current source\n')
        self.config['runtime_sha256'] = {'current.py': sha_file(current_source)}
        self.retained_other_route()
        current = dict(self.plan, candidate_id='current-candidate',
                       evaluation_runtime_sha256=dict(self.config['runtime_sha256']))
        current.pop('plan_id')
        current['plan_id'] = digest(current)
        current_folder = self.folder.parent / 'current'
        current_folder.mkdir()
        current_path = current_folder / 'plan.json'
        current_path.write_text(json.dumps(current))
        launch = Mock()
        result = self.step(self.engine(), launch=launch)
        self.assertEqual(result['counts'], {'PENDING': 2})
        self.assertEqual((result['observed'], result['censored']), (1, 0))
        self.assertEqual(result['unrouted_retained_plan_holds'], [{
            'plan_id': self.plan['plan_id'], 'candidate_id': self.plan['candidate_id'],
            'reason': 'UNROUTED_RETAINED_EVALUATOR_SOURCE'}])
        launch.assert_called_once_with(current_path, 0)
        self.assertEqual(self.files(), before)
        self.assertFalse((current_folder / 'qualification.json').exists())

    def test_current_declared_source_tampering_still_rejects(self):
        self.retained_other_route()
        original_plan = self.path.read_bytes()
        self.pinned.write_text('# changed after declared pin\n')
        result = self.step(self.engine())
        self.assertEqual(result['counts'], {'REJECTED': 1})
        self.assertEqual(result['unrouted_retained_plan_holds'], [])
        self.assertEqual(read_json(self.folder / 'qualification.json')['reasons'],
                         ['EVALUATOR_SOURCE_UNPINNED_OR_CHANGED'])
        self.assertEqual(self.path.read_bytes(), original_plan)
        self.assertEqual(list(self.folder.glob('outcome-*.json')), [])

    def test_suppression_receipt_uses_the_same_thirty_second_window(self):
        self.make_futile()
        for slot in range(3):
            self.outcome(slot, evaluated_at_ms=self.now-90001)
        before = self.files()
        receipt = self.folder / 'futility-suppression-003.json'
        for offset, accepted in ((-90000, False), (-30001, False), (-30000, True),
                                 (self.plan['rule']['input_grace_ms'], True),
                                 (self.plan['rule']['input_grace_ms']+1, False)):
            with self.subTest(offset=offset):
                if accepted:
                    binding = evaluation._futility_suppression_receipt(
                        self.path, self.plan, 3, at_ms=self.now+offset)
                    self.assertEqual(binding['at_ms'], self.now+offset)
                    self.assertEqual(binding, evaluation._futility_suppression_receipt(
                        self.path, self.plan, 3))
                    receipt.unlink()
                else:
                    with self.assertRaisesRegex(ContractError, 'FUTILITY_SUPPRESSION_RECEIPT_CHANGED'):
                        evaluation._futility_suppression_receipt(
                            self.path, self.plan, 3, at_ms=self.now+offset)
                    self.assertFalse(receipt.exists())
                self.assertEqual(self.files(), before)

    def test_actual_suppression_is_immutable_and_preserves_prior_denial(self):
        self.make_futile()
        engine = self.engine()
        before = {p.name: p.read_bytes() for p in self.folder.glob('outcome-*.json')}
        with patch.object(evaluation.time, 'time', return_value=(self.now - 1000) / 1000):
            engine.record_admission_deferral(self.path, 3, ['HOST_FULL_MEMORY_PSI_PRESSURE'])
        denial = self.folder / 'admission-denial-003.json'
        denial_bytes = denial.read_bytes()
        result = self.step(engine)
        receipt = self.folder / 'futility-suppression-003.json'
        raw = receipt.read_bytes()
        value = read_json(receipt)
        self.assertEqual(value['at_ms'], self.now)
        self.assertFalse(value['resource_admission_attempted_at_suppression'])
        self.assertEqual([item['slot'] for item in value['basis_outcomes']], [0, 1, 2])
        self.assertEqual(result['new_prediction_launches_suppressed'], 1)
        self.step(engine, self.now + 1000)
        self.assertEqual(receipt.read_bytes(), raw)
        self.step(engine, self.now + self.plan['rule']['input_grace_ms'] + 1)
        result = read_json(self.folder / 'outcome-003.json')
        self.assertEqual(result['reason'], 'NO_PREDICTION_IN_FIXED_SLOT')
        self.assertEqual(result['no_prediction_disposition'], 'FUTILITY_SUPPRESSED')
        self.assertEqual(result['futility_suppression_sha256'], sha_file(receipt))
        self.assertEqual(result['first_admission_denial_sha256'], sha_file(denial))
        self.assertEqual(denial.read_bytes(), denial_bytes)
        for name, original in before.items():
            self.assertEqual((self.folder / name).read_bytes(), original)

    def test_expired_unvisited_slot_never_backfills_a_suppression(self):
        self.make_futile()
        self.step(self.engine(), self.now + self.plan['rule']['input_grace_ms'] + 1)
        result = read_json(self.folder / 'outcome-003.json')
        self.assertEqual(result['no_prediction_disposition'], 'NO_LOCAL_ADMISSION_OR_FUTILITY_RECEIPT')
        self.assertEqual(result['reason'], 'NO_PREDICTION_IN_FIXED_SLOT')
        self.assertFalse((self.folder / 'futility-suppression-003.json').exists())

    def test_changed_basis_fails_before_new_outcome(self):
        self.make_futile()
        engine = self.engine()
        self.step(engine)
        source = self.folder / 'outcome-000.json'
        value = read_json(source)
        value['reason'] = 'CHANGED_AFTER_SUPPRESSION'
        source.write_text(json.dumps(value))
        with self.assertRaisesRegex(ContractError, 'FUTILITY_SUPPRESSION_BASIS_CHANGED'):
            self.step(engine, self.now + self.plan['rule']['input_grace_ms'] + 1)
        self.assertFalse((self.folder / 'outcome-003.json').exists())
        self.assertFalse((self.folder / 'qualification.json').exists())

    def test_changed_receipt_fails_before_new_outcome(self):
        self.make_futile()
        engine = self.engine()
        self.step(engine)
        receipt = self.folder / 'futility-suppression-003.json'
        value = read_json(receipt)
        value['at_ms'] += 1
        receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(ContractError, 'FUTILITY_SUPPRESSION_RECEIPT_CHANGED'):
            self.step(engine, self.now + 1000)
        with self.assertRaisesRegex(ContractError, 'FUTILITY_SUPPRESSION_RECEIPT_CHANGED'):
            self.step(engine, self.now + self.plan['rule']['input_grace_ms'] + 1)
        self.assertFalse((self.folder / 'outcome-003.json').exists())

    def test_late_scan_uses_actual_clock_and_does_not_invent_in_window_event(self):
        self.make_futile()
        engine = self.engine()
        after = self.now + self.plan['rule']['input_grace_ms'] + 1
        ticks = iter([self.now])
        def clock():
            return next(ticks, after) / 1000
        with patch.object(evaluation.time, 'time', side_effect=clock), \
                patch.object(engine, 'launch', side_effect=AssertionError('No launch')), \
                patch.object(evaluation, 'read_quotes', side_effect=AssertionError('No source scan')), \
                contextlib.redirect_stdout(io.StringIO()):
            result = engine.step()
        self.assertEqual(result['new_prediction_launches_suppressed'], 0)
        self.assertFalse((self.folder / 'futility-suppression-003.json').exists())
        self.assertFalse((self.folder / 'outcome-003.json').exists())
        self.step(engine, after)
        self.assertEqual(read_json(self.folder / 'outcome-003.json')['no_prediction_disposition'],
                         'NO_LOCAL_ADMISSION_OR_FUTILITY_RECEIPT')

    def test_invalid_time_and_nonfutile_basis_cannot_create_receipt(self):
        with self.assertRaisesRegex(ContractError, 'FUTILITY_SUPPRESSION_NOT_IRREVERSIBLE'):
            evaluation._futility_suppression_receipt(self.path, self.plan, 0, at_ms=self.now)
        self.make_futile()
        for when in (self.now - 90001, self.now + self.plan['rule']['input_grace_ms'] + 1):
            with self.subTest(when=when), self.assertRaisesRegex(ContractError, 'FUTILITY_SUPPRESSION_RECEIPT_CHANGED'):
                evaluation._futility_suppression_receipt(self.path, self.plan, 3, at_ms=when)
        self.outcome(0, evaluated_at_ms=self.now + 1)
        with self.assertRaisesRegex(ContractError, 'FUTILITY_SUPPRESSION_BASIS_CHANGED'):
            evaluation._futility_suppression_receipt(self.path, self.plan, 3, at_ms=self.now)
        self.assertFalse((self.folder / 'futility-suppression-003.json').exists())


    def test_minimal_basis_preserves_all_records_and_validates_unselected_records(self):
        self.plan['start_ms'] = self.now - 8 * self.plan['rule']['slot_spacing_ms']
        self.write_plan()
        for slot in range(8):
            self.outcome(slot, status='OBSERVED' if slot in (1, 4) else 'CENSORED')
        before = self.files()
        binding = evaluation._futility_suppression_receipt(self.path, self.plan, 8, at_ms=self.now)
        receipt = self.folder / 'futility-suppression-008.json'
        value = read_json(receipt)
        self.assertEqual([entry['slot'] for entry in value['basis_outcomes']], [0, 2, 3])
        self.assertEqual(len(value['basis_outcomes']),
            min(self.plan['rule']['max_censored'] + 1,
                self.plan['rule']['slots'] - self.plan['rule']['min_observed'] + 1))
        self.assertEqual(binding, evaluation._futility_suppression_receipt(self.path, self.plan, 8))
        for name, original in before.items():
            self.assertEqual((self.folder / name).read_bytes(), original)
        unselected = self.folder / 'outcome-007.json'
        changed = read_json(unselected)
        changed['candidate_id'] = 'INVALID_UNSELECTED_RECORD'
        unselected.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ContractError, 'FUTILITY_OUTCOME_IDENTITY_OR_STATUS'):
            evaluation._futility_suppression_receipt(self.path, self.plan, 9,
                at_ms=self.now + self.plan['rule']['slot_spacing_ms'])
        self.assertFalse((self.folder / 'futility-suppression-009.json').exists())

    def test_basis_requires_prior_original_censors_with_actual_timestamps(self):
        self.make_futile()
        for bad in ({'evaluated_at_ms': None},
                    {'no_prediction_disposition': 'FUTILITY_SUPPRESSED'}):
            with self.subTest(bad=bad):
                self.outcome(0, **bad)
                with self.assertRaisesRegex(ContractError,
                        'FUTILITY_SUPPRESSION_(BASIS_CHANGED|NOT_IRREVERSIBLE)'):
                    evaluation._futility_suppression_receipt(self.path, self.plan, 3,
                        at_ms=self.now)
                self.assertFalse((self.folder / 'futility-suppression-003.json').exists())
        self.outcome(0)
        (self.folder / 'outcome-002.json').unlink()
        self.outcome(4)
        with self.assertRaisesRegex(ContractError, 'FUTILITY_SUPPRESSION_BASIS_CHANGED'):
            evaluation._futility_suppression_receipt(self.path, self.plan, 3, at_ms=self.now)
        self.assertFalse((self.folder / 'futility-suppression-003.json').exists())


if __name__ == '__main__':
    unittest.main()
