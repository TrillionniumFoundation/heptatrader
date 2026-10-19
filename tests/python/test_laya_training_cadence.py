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


if __name__ == '__main__':
    unittest.main()
