"""Cadence must refuse before archive decoding and the shared worker lease."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch
from laya_cell.integrity import ContractError
from laya_cell.promotion import PROTOCOL
from laya_cell.resident import Supervisor, MIN_CADENCE_MS
from laya_cell.resident_store import database, put_meta
from historical_training.resident_resource_guard import guarded_trainer_class

class TrainingCadenceTests(unittest.TestCase):
    def service(self, guarded=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        gate = MagicMock()
        gate.status_diagnostics = None
        gate.acquire.return_value = True
        cls = guarded_trainer_class(Supervisor, gate) if guarded else Supervisor
        service = cls.__new__(cls)
        service.root = Path(temporary.name)
        service.c = {'promotion_protocol': PROTOCOL}
        service.storage = None
        service.db = database(service.root/'db')
        self.addCleanup(service.db.close)
        with service.db:
            put_meta(service.db, 'last_job_created_ms', 2*MIN_CADENCE_MS)
        return service, gate

    def environment(self, service, now):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('laya_cell.resident.time.time', return_value=now/1000))
        stack.enter_context(patch('laya_cell.resident.config_check'))
        stack.enter_context(patch('laya_cell.resident.bootstrap_still_off'))
        stack.enter_context(patch('laya_cell.resident.inspect_storage', return_value={'admission_ready': True}))
        rewards = stack.enter_context(patch.object(service, 'sync_rewards', return_value={'reason': 'fixture'}))
        stack.enter_context(patch.object(service, 'reconcile_evaluation_queue', return_value={
            'deferred_training_candidates': 0, 'viable_pending_evaluations': 0,
            'pending_original_trials': 0}))
        stack.enter_context(patch.object(service, 'write_status', side_effect=lambda **fields: fields))
        collect = stack.enter_context(patch('laya_cell.resident.collect', side_effect=RuntimeError('FULL_COLLECT_REACHED')))
        return rewards, collect

    def test_guarded_wait_updates_bookkeeping_without_lease_or_source_read(self):
        service, gate = self.service(guarded=True)
        rewards, collect = self.environment(service, 3*MIN_CADENCE_MS-1)
        result = service.step()
        self.assertEqual(result['reason'], 'TRAINING_CADENCE_NOT_DUE')
        self.assertEqual(result['next_training_due_ms'], 3*MIN_CADENCE_MS)
        self.assertFalse(result['training_data_refresh_performed'])
        rewards.assert_called_once()
        collect.assert_not_called()
        gate.acquire.assert_not_called()
        self.assertEqual(service.db.execute('SELECT count(*) FROM consumed_rows').fetchone()[0], 0)

    def test_unguarded_entry_also_waits_before_collect(self):
        service, _ = self.service()
        rewards, collect = self.environment(service, 3*MIN_CADENCE_MS-1)
        self.assertEqual(service.step()['reason'], 'TRAINING_CADENCE_NOT_DUE')
        rewards.assert_called_once()
        collect.assert_not_called()

    def test_exact_due_time_reenters_original_guard_and_collect(self):
        service, gate = self.service(guarded=True)
        _, collect = self.environment(service, 3*MIN_CADENCE_MS)
        with self.assertRaisesRegex(RuntimeError, 'FULL_COLLECT_REACHED'):
            service.step()
        gate.acquire.assert_called_once()
        gate.release.assert_called_once()
        collect.assert_called_once()

    def test_clock_regression_fails_before_worker(self):
        service, gate = self.service(guarded=True)
        _, collect = self.environment(service, 2*MIN_CADENCE_MS-1)
        with self.assertRaisesRegex(ContractError, 'TRAINING_CLOCK_REGRESSION'):
            service.step()
        gate.acquire.assert_not_called()
        collect.assert_not_called()

class ForwardRegistrationWaitStatusTests(unittest.TestCase):
    @staticmethod
    def subject():
        class FakeBase:
            def write_status(self, **extra):
                return extra
        class FakeGate:
            pass
        from historical_training.resident_resource_guard import guarded_trainer_class
        cls = guarded_trainer_class(FakeBase, FakeGate())
        trainer = cls.__new__(cls)
        trainer._forward_registration_preflight = {
            'reasons': ['HOST_SWAP_RESERVE_BELOW_1GIB'],
            'at_ms': 123,
        }
        return trainer

    def test_resource_wait_replaces_stale_original_trial_reason(self):
        result = self.subject().write_status(
            pending_original_trials=0,
            deferred_training_candidates=1,
            forward_registration_wait_reasons=['PRIOR_ORIGINAL_TRIAL_PENDING'],
        )
        self.assertEqual(result['forward_registration_wait_reasons'],
                         ['HOST_SWAP_RESERVE_BELOW_1GIB'])
        self.assertEqual(result['forward_registration_wait_checked_at_ms'], 123)

    def test_pending_original_trial_keeps_its_wait_reason(self):
        result = self.subject().write_status(
            pending_original_trials=1,
            deferred_training_candidates=1,
            forward_registration_wait_reasons=['PRIOR_ORIGINAL_TRIAL_PENDING'],
        )
        self.assertEqual(result['forward_registration_wait_reasons'],
                         ['PRIOR_ORIGINAL_TRIAL_PENDING'])



class CandidateRegistrationStatusTests(unittest.TestCase):
    def service(self, state='NO_FORWARD_TRIAL', publish=True, guarded=True):
        import json
        from laya_cell.integrity import canonical, digest
        from laya_cell.resident_plan_admission import KEY_PREFIX, PROFILE, RUNTIME_PIN, _identity
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        gate = MagicMock()
        gate.status_diagnostics = None
        gate.acquire.return_value = False
        gate.last_admission = {'at_ms': 200, 'admit_new_worker': False,
                               'reasons': ['HOST_SWAP_RESERVE_BELOW_1GIB']}
        cls = guarded_trainer_class(Supervisor, gate) if guarded else Supervisor
        service = cls.__new__(cls)
        service.root = Path(temporary.name)
        service.storage = None
        service.c = {'promotion_protocol': PROTOCOL,
                     'evaluation_directory': str(service.root/'evaluation'),
                     'future_plan_admission_profile': PROFILE,
                     'future_plan_admission_effective_at_ms': 100,
                     'runtime_sha256': {name: 'a'*64 for name in RUNTIME_PIN}}
        service.db = database(service.root/'db')
        self.addCleanup(service.db.close)
        checker = patch('laya_cell.resident_plan_admission.check_config')
        checker.start()
        self.addCleanup(checker.stop)
        candidate = {'window': {'training_end_ms': 100, 'validation_start_ms': 101,
                                'validation_end_ms': 86400100}, 'parent_id': 'parent'}
        candidate['candidate_id'] = digest(candidate)
        output = service.root/'candidate'
        output.mkdir()
        (output/'candidate.json').write_bytes(canonical(candidate)+b'\n')
        job = {'output_directory': str(output), 'parent_id': 'parent'}
        candidate_id = candidate['candidate_id']
        with service.db:
            service.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',
                               ('job', 'fingerprint', 'COMPLETED', 100,
                                candidate_id, json.dumps(job)))
            put_meta(service.db, 'last_completed_candidate', candidate_id)
        identity = _identity(service, 'job', candidate, job)
        record = {**identity, 'state': state, 'checked_at_ms': 200,
                  'trial_allocated': state in ('RESERVED', 'PUBLISHED'),
                  'reason': 'NO_CONTINUOUS_OPEN_WINDOW_FITS',
                  'calendar': {'reason': 'NO_CONTINUOUS_OPEN_WINDOW_FITS',
                               'earliest_feasible_start_ms': None,
                               'latest_feasible_start_ms': None,
                               'plan_interval_ms': [200, 300]}}
        if record['trial_allocated']:
            plan = {'job_id': 'job', 'candidate_id': candidate_id,
                    'candidate_manifest_sha256': identity['candidate_manifest_sha256'],
                    'trial': 43, 'start_ms': 200, 'outcome_end_ms': 300}
            plan['plan_id'] = digest(plan)
            record['plan'] = plan
        record['registration_id'] = digest(record)
        with service.db:
            put_meta(service.db, KEY_PREFIX+'job', record)
        folder = Path(service.c['evaluation_directory'])/'job'
        folder.mkdir(parents=True)
        if publish and state == 'NO_FORWARD_TRIAL':
            (folder/'no-forward-trial.json').write_bytes(canonical(record)+b'\n')
        return service, gate, candidate_id, record, folder

    def held_step(self, service, gate):
        before = service.db.total_changes
        with ExitStack() as stack:
            # Status tests run in a real feasible session; window refusal has its own tests.
            stack.enter_context(patch('laya_cell.resident.time.time', return_value=1792396800))
            stack.enter_context(patch('laya_cell.resident.config_check'))
            stack.enter_context(patch('laya_cell.resident.bootstrap_still_off'))
            storage = stack.enter_context(patch('laya_cell.resident.inspect_storage',
                                                 return_value={'admission_ready': True}))
            stack.enter_context(patch.object(service, 'sync_rewards', return_value={}))
            stack.enter_context(patch.object(service, 'reconcile_evaluation_queue', return_value={
                'deferred_training_candidates': 0, 'viable_pending_evaluations': 0,
                'pending_original_trials': 0, 'completed_candidates_without_forward_trial': 1,
                'forward_registration_wait_reasons': []}))
            collect = stack.enter_context(patch('laya_cell.resident.collect',
                                                 side_effect=AssertionError('FULL_COLLECT_REACHED')))
            stack.enter_context(patch('builtins.print'))
            result = service.step()
        storage.assert_called_once()
        collect.assert_not_called()
        gate.acquire.assert_called_once()
        gate.release.assert_not_called()
        gate.check.assert_not_called()
        self.assertEqual(service.db.total_changes, before)
        return result

    def test_terminal_candidate_and_current_resource_wait_are_both_visible(self):
        service, gate, candidate_id, _, _ = self.service()
        result = self.held_step(service, gate)
        self.assertEqual(result['state'], 'HOLD_NEW_TRAINING_RESOURCE_OR_SOURCE')
        self.assertEqual(result['waiting_stage'], 'NEW_TRAINING_RESOURCE_PREFLIGHT')
        self.assertFalse(result['training_data_refresh_performed'])
        self.assertFalse(result['worker_lease_acquired'])
        self.assertEqual(result['completed_candidates_without_forward_trial'], 1)
        self.assertEqual(result['deferred_training_candidates'], 0)
        self.assertIsInstance(result['evaluation_queue_checked_at_ms'], int)
        registration = result['latest_candidate_registration']
        self.assertEqual(registration['candidate_id'], candidate_id)
        self.assertEqual(registration['parent_id'], 'parent')
        self.assertEqual(registration['state'], 'NO_FORWARD_TRIAL')
        self.assertTrue(registration['receipt_publication_verified'])
        self.assertEqual(registration['future_observations'], 0)
        self.assertFalse(registration['registration_retryable'])
        self.assertFalse(registration['registration_is_performance_result'])

    def test_terminal_file_publication_and_conflicts_are_not_conflated(self):
        from laya_cell.integrity import canonical
        service, _, candidate_id, record, folder = self.service(publish=False)
        pending = service._latest_candidate_registration(candidate_id)
        self.assertEqual(pending['state'], 'TERMINAL_RECEIPT_PUBLICATION_PENDING')
        self.assertNotIn('future_observations', pending)
        receipt = folder/'no-forward-trial.json'
        receipt.write_bytes(canonical(record)+b'\n')
        self.assertTrue(service._latest_candidate_registration(candidate_id)['receipt_publication_verified'])
        receipt.write_text('{}\n')
        changed = service._latest_candidate_registration(candidate_id)
        self.assertEqual(changed['reason'], 'FORWARD_TERMINAL_RECEIPT_CHANGED')
        self.assertNotIn('future_observations', changed)
        receipt.write_bytes(canonical(record)+b'\n')
        (folder/'plan.json').write_text('{}\n')
        conflict = service._latest_candidate_registration(candidate_id)
        self.assertEqual(conflict['reason'], 'FORWARD_TERMINAL_PLAN_CONFLICT')
        self.assertNotIn('future_observations', conflict)

    def test_nonterminal_registration_does_not_claim_observations_or_qualification(self):
        for state in ('DEFERRED', 'RESERVED', 'PUBLISHED'):
            with self.subTest(state=state):
                service, _, candidate_id, record, _ = self.service(state=state)
                result = service._latest_candidate_registration(candidate_id)
                self.assertEqual(result['state'], state)
                self.assertNotIn('future_observations', result)
                self.assertNotIn('receipt_publication_verified', result)
                self.assertFalse(result['registration_is_performance_result'])
                if state != 'DEFERRED':
                    self.assertEqual(result['plan_id'], record['plan']['plan_id'])
                    self.assertEqual(result['trial'], 43)

    def test_ambiguous_or_other_candidate_does_not_inherit_registration(self):
        service, _, candidate_id, _, _ = self.service()
        with service.db:
            service.db.execute("INSERT INTO jobs SELECT 'second','second',status,created_ms,candidate_id,payload FROM jobs WHERE id='job'")
        for value in (candidate_id, 'different-candidate'):
            with self.subTest(candidate=value):
                result = service._latest_candidate_registration(value)
                self.assertEqual(result['candidate_id'], value)
                self.assertEqual(result['reason'], 'LATEST_CANDIDATE_JOB_BINDING')
                self.assertNotIn('future_observations', result)

    def test_malformed_job_diagnostic_does_not_stop_current_hold_publication(self):
        service, gate, _, _, _ = self.service()
        with service.db:
            service.db.execute("UPDATE jobs SET payload='[]' WHERE id='job'")
        result = self.held_step(service, gate)
        self.assertEqual(result['state'], 'HOLD_NEW_TRAINING_RESOURCE_OR_SOURCE')
        self.assertEqual(result['latest_candidate_registration']['reason'], 'LATEST_CANDIDATE_JOB_SHAPE')

    def test_changed_manifest_or_sqlite_diagnostic_error_is_unavailable(self):
        import sqlite3
        service, _, candidate_id, _, _ = self.service()
        (service.root/'candidate'/'candidate.json').write_text('[]\n')
        result = service._latest_candidate_registration(candidate_id)
        self.assertEqual(result['reason'], 'LATEST_CANDIDATE_MANIFEST_SHAPE')
        db = MagicMock()
        db.execute.side_effect = sqlite3.OperationalError('fixture busy')
        with patch.object(service, 'db', db):
            result = service._latest_candidate_registration(candidate_id)
        self.assertEqual(result['state'], 'REGISTRATION_EVIDENCE_UNAVAILABLE')
        self.assertNotIn('future_observations', result)

    def test_queue_check_time_is_preserved_and_explicit_candidate_stays_bound(self):
        service, _, _, _, _ = self.service()
        service._evaluation_queue_status = {'pending_original_trials': 1,
                                            'evaluation_queue_checked_at_ms': 123}
        with patch('builtins.print'):
            result = service.write_status(storage={'admission_ready': True},
                                          pending_original_trials=2,
                                          last_candidate_id='new-candidate')
        self.assertEqual(result['pending_original_trials'], 2)
        self.assertEqual(result['evaluation_queue_checked_at_ms'], 123)
        self.assertEqual(result['last_candidate_id'], 'new-candidate')
        self.assertEqual(result['latest_candidate_registration']['candidate_id'], 'new-candidate')
        self.assertEqual(result['latest_candidate_registration']['state'], 'REGISTRATION_EVIDENCE_UNAVAILABLE')

    def test_unenrolled_profile_does_not_read_database(self):
        service = Supervisor.__new__(Supervisor)
        service.c = {}
        self.assertIsNone(service._latest_candidate_registration('candidate'))

if __name__ == '__main__':
    unittest.main()
