"""Blocked control phases invalidate old inference without losing its live worker."""
import signal
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import test_driver_inference_modes as modes


class DriverWorkerLifecycleTests(unittest.TestCase):
    setUp = modes.DriverInferenceModesTests.setUp
    harness = modes.DriverInferenceModesTests.harness
    make_model_runtime = modes.DriverInferenceModesTests.make_model_runtime

    def test_active_order_never_starts_flat_or_exposed_worker(self):
        for position in (0.0, 100000.0):
            with self.subTest(position=position):
                self.state['position'] = self.snap['position'] = position
                self.snap['active'] = ['existing-broker-order']
                with self.harness(['--once']) as stack:
                    stack.enter_context(patch.object(self.d, 'entry_inference_hold_reason', return_value=None))
                    self.assertEqual(self.d.main(), 0)
                    self.worker.assert_not_called()
                    self.submit.assert_not_called()
                self.assertEqual(self.state['model_inference_pause']['reason_code'], 'ACTIVE_BROKER_ORDER')

    def test_mutation_gate_blocks_flat_worker_with_explicit_reason(self):
        self.snap['owner_health']['mutation_ready'] = False
        with self.harness(['--once']) as stack:
            stack.enter_context(patch.object(self.d, 'entry_inference_hold_reason', return_value=None))
            self.assertEqual(self.d.main(), 0)
            self.worker.assert_not_called()
            self.quote.assert_not_called()
            self.submit.assert_not_called()
        self.assertEqual(self.state['model_inference_pause']['reason_code'], 'AUTHORITATIVE_MUTATION_NOT_READY')

    def exercise_blocked_worker(self, mode):
        self.make_model_runtime()
        self.state['active_lease_continuation_supported'] = False
        ticks = [0]
        handlers = {}
        first = Mock()
        first.done.side_effect = lambda: ticks[0] >= 4
        first.observed_at_ms = self.clock[0]
        first.position = 0.0
        first.result.return_value = {'action': 'BUY', 'confidence': 1.0, 'rationale': 'pre-hold answer'}
        second = Mock()
        second.done.return_value = False
        created = []
        overlap = []

        def worker(*args):
            if created:
                overlap.append(not first.done())
                created.append(second)
                return second
            created.append(first)
            return first

        def sleep(_seconds):
            ticks[0] += 1
            self.clock[0] += 10000
            if ticks[0] == 1:
                if mode == 'pending':
                    self.state['pending'] = {'command_id': 'original', 'kind': 'OPEN'}
                elif mode == 'active':
                    self.snap['active'] = ['original-broker-order']
                elif mode == 'owner':
                    self.state['owner_fence_recovery_required'] = True
            if ticks[0] == 2:
                self.snap['active'] = []
            if ticks[0] == 5:
                handlers[signal.SIGTERM](signal.SIGTERM, None)

        def inspect(*args):
            self.state['pending'] = None

        def recover(*args):
            self.state['owner_fence_recovery_required'] = False
            self.state['halt_new_entries'] = False
            return True

        def renew(*args):
            if mode == 'owner_during_renewal' and ticks[0] == 1:
                self.state['owner_fence_recovery_required'] = True

        with self.harness([]) as stack:
            stack.enter_context(patch.object(self.d.signal, 'signal', side_effect=lambda sig, fn: handlers.__setitem__(sig, fn)))
            stack.enter_context(patch.object(self.d.time, 'sleep', side_effect=sleep))
            stack.enter_context(patch.object(self.d, 'entry_inference_hold_reason', return_value=None))
            stack.enter_context(patch.object(self.d, 'DECISION_SEC', 0))
            stack.enter_context(patch.object(self.d, 'inspect_pending', side_effect=inspect))
            stack.enter_context(patch.object(self.d, 'recover_owner', side_effect=recover))
            stack.enter_context(patch.object(self.d, 'run', return_value=SimpleNamespace(returncode=0, stdout='{}', stderr='')))
            stack.enter_context(patch.object(self.d, 'record_entry_guard', return_value={'reason': 'fixture'}))
            self.renew.side_effect = renew
            self.worker.side_effect = worker
            self.assertEqual(self.d.main(), 0)
            self.assertEqual(self.worker.call_count, 2)
            self.submit.assert_not_called()
        self.assertEqual(overlap, [False])
        first.result.assert_not_called()
        self.assertEqual(self.state['decisions'], 0)

    def test_pending_reconciliation_invalidates_answer_and_retains_inflight_worker(self):
        self.exercise_blocked_worker('pending')

    def test_active_order_invalidates_answer_and_retains_inflight_worker(self):
        self.exercise_blocked_worker('active')

    def test_owner_recovery_retains_inflight_worker_through_resumption(self):
        self.exercise_blocked_worker('owner')

    def test_late_renewal_fence_retains_inflight_worker_through_resumption(self):
        self.exercise_blocked_worker('owner_during_renewal')

    def test_command_prepared_this_iteration_blocks_next_worker(self):
        self.make_model_runtime()
        ticks = [0]
        handlers = {}
        answer = Mock()
        answer.done.side_effect = lambda: ticks[0] >= 1
        answer.observed_at_ms = self.clock[0]
        answer.position = 0.0
        answer.result.return_value = {'action': 'BUY', 'confidence': 1.0, 'rationale': 'fresh fixture'}

        def sleep(_seconds):
            ticks[0] += 1
            self.clock[0] += 10000
            if ticks[0] == 2:
                handlers[signal.SIGTERM](signal.SIGTERM, None)

        def prepare(*args):
            self.state['pending'] = {'command_id': 'new-original-command', 'kind': 'OPEN'}

        with self.harness([]) as stack:
            stack.enter_context(patch.object(self.d.signal, 'signal', side_effect=lambda sig, fn: handlers.__setitem__(sig, fn)))
            stack.enter_context(patch.object(self.d.time, 'sleep', side_effect=sleep))
            stack.enter_context(patch.object(self.d, 'entry_inference_hold_reason', return_value=None))
            stack.enter_context(patch.object(self.d, 'DECISION_SEC', 0))
            stack.enter_context(patch.object(self.d, 'run', return_value=SimpleNamespace(returncode=0, stdout='{}', stderr='')))
            stack.enter_context(patch.object(self.d, 'record_entry_guard', return_value={'reason': 'fixture'}))
            self.worker.return_value = answer
            self.submit.side_effect = prepare
            self.assertEqual(self.d.main(), 0)
            self.worker.assert_called_once()
            self.submit.assert_called_once()
        self.assertEqual(self.state['decisions'], 1)
        self.assertEqual(self.state['model_inference_pause']['reason_code'], 'ORIGINAL_COMMAND_PENDING')

    def test_completed_blocked_worker_is_released_without_consuming_answer(self):
        worker = Mock()
        worker.done.return_value = True
        retained, discarded = self.d.sync_model_inference(self.state, worker, False, 'ACTIVE_BROKER_ORDER')
        self.assertIsNone(retained)
        self.assertTrue(discarded)
        worker.result.assert_not_called()


if __name__ == '__main__':
    unittest.main()
