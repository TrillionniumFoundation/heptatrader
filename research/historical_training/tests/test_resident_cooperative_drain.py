"""Exercise guard.main with finite fake children; no model or worker runs."""
import os
from pathlib import Path
import signal
import sys
import tempfile
from types import ModuleType
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from historical_training import resident_resource_guard as guard


class EndObservation(BaseException):
    pass


class Child:
    def __init__(self):
        self.running = True
        self.poll_error = None
        self.waits = 0

    def poll(self):
        if self.poll_error is not None:
            error, self.poll_error = self.poll_error, None
            raise error
        return None if self.running else 0

    def wait(self, timeout):
        if self.running:
            raise AssertionError('cannot reap a running fake child')
        self.waits += 1
        return 0


class CooperativeDrainTests(unittest.TestCase):
    def run_controller(self, *, once=False, drain=True, step_error=None,
                       poll_error=None, missing=None, stop=False,
                       report_error=False, abort_wait=False, drain_at_recover=False,
                       stop_during_wait=False):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            root = Path(td)
            old_mask = os.umask(0o077)
            stack.callback(os.umask, old_mask)
            original_path = list(sys.path)
            stack.callback(sys.path.__setitem__, slice(None), original_path)
            handlers, events = {}, []
            gate = Mock(root=root, draining=False)
            child = Child()
            child.poll_error = poll_error
            service = Mock(stop=False, child=None, _pending_worker_completion=None)
            service.db = Mock()
            service.recover = Mock()
            service.step = Mock()
            service.reap_pending_worker = Mock()
            sleep_count = 0

            def assert_owned():
                os.fstat(service._controller_lock_fd)
                gate.release.assert_not_called()

            def request_drain():
                handlers[signal.SIGUSR1](signal.SIGUSR1, None)

            def recover():
                if drain_at_recover:
                    request_drain()

            def step():
                assert_owned()
                events.append('step')
                service.child = None if missing == 'child' else child
                service._pending_worker_completion = (None if missing == 'pending'
                                                       else {'job_id': 'original'})
                if drain:
                    request_drain()
                if stop:
                    service.stop = True
                if step_error is not None:
                    raise step_error

            def reap():
                assert_owned()
                events.append('reap')
                if service._pending_worker_completion is None:
                    return None
                if service.child is None:
                    raise RuntimeError('PENDING_WORKER_IDENTITY_LOST')
                if service.child.poll() is None:
                    return {'state': 'WORKER_TERMINATION_PENDING'}
                service.child.wait(timeout=0)
                service.child = service._pending_worker_completion = None
                events.append('completed')

            def sleep(_):
                nonlocal sleep_count
                assert_owned()
                sleep_count += 1
                if abort_wait or sleep_count > 35:
                    raise EndObservation()
                if stop_during_wait:
                    service.stop = True
                    return
                child.running = False

            def terminate(actual):
                self.assertIs(actual, child)
                events.append('terminate')
                child.running = False
                return 0

            def release():
                os.fstat(service._controller_lock_fd)
                if service.child is not None:
                    self.assertFalse(service.child.running)
                events.append('release')

            service.recover.side_effect = recover
            service.step.side_effect = step
            service.reap_pending_worker.side_effect = reap
            gate.release.side_effect = release
            resident = ModuleType('laya_cell.resident')
            resident.Supervisor = object
            resident.terminate_worker = Mock(side_effect=terminate)
            stack.enter_context(patch.dict(sys.modules, {'laya_cell.resident': resident}))
            stack.enter_context(patch.object(guard, 'read', return_value={
                'original_config': str(root / 'original.json')}))
            stack.enter_context(patch.object(guard, 'verify_config', return_value={
                'runtime_directory': str(root / 'runtime'), 'control_directory': str(root)}))
            stack.enter_context(patch.object(guard, 'Gate', return_value=gate))
            stack.enter_context(patch.object(guard, 'guarded_trainer_class',
                                            return_value=lambda _: service))
            stack.enter_context(patch.object(guard.signal, 'signal',
                                            side_effect=lambda sig, fn: handlers.setdefault(sig, fn)))
            stack.enter_context(patch.object(guard.time, 'sleep', side_effect=sleep))
            stack.enter_context(patch.object(guard, 'atomic',
                side_effect=RuntimeError('error status failed') if report_error else None))
            args = ['guard', '--config', str(root / 'wrapper.json'), '--role', 'trainer']
            stack.enter_context(patch.object(sys, 'argv', args + (['--once'] if once else [])))
            outcome = None
            try:
                guard.main()
            except BaseException as exc:
                outcome = exc
            service.db.close.assert_called_once()
            with self.assertRaises(OSError):
                os.fstat(service._controller_lock_fd)
            return service, gate, child, resident.terminate_worker, events, outcome

    def test_drain_reaps_original_pending_worker_before_exit_without_another_step(self):
        service, gate, child, terminate, events, outcome = self.run_controller()
        self.assertIsNone(outcome)
        self.assertEqual(service.step.call_count, 1)
        self.assertGreaterEqual(service.reap_pending_worker.call_count, 2)
        self.assertEqual(child.waits, 1)
        terminate.assert_not_called()
        gate.release.assert_called_once()
        self.assertLess(events.index('completed'), events.index('release'))

    def test_once_does_not_override_simultaneous_drain_of_pending_worker(self):
        service, gate, child, terminate, _, outcome = self.run_controller(once=True)
        self.assertIsNone(outcome)
        self.assertEqual(service.step.call_count, 1)
        self.assertEqual(child.waits, 1)
        terminate.assert_not_called()
        gate.release.assert_called_once()

    def test_once_original_step_error_is_raised_only_after_natural_reap(self):
        error = RuntimeError('original step failed')
        _, gate, child, terminate, events, outcome = self.run_controller(
            once=True, step_error=error)
        self.assertIs(outcome, error)
        self.assertEqual(child.waits, 1)
        terminate.assert_not_called()
        self.assertLess(events.index('completed'), events.index('release'))
        gate.release.assert_called_once()

    def test_drain_step_error_without_once_still_waits_for_pending_child(self):
        _, gate, child, terminate, _, outcome = self.run_controller(
            step_error=RuntimeError('original step failed'))
        self.assertIsNone(outcome)
        self.assertEqual(child.waits, 1)
        terminate.assert_not_called()
        gate.release.assert_called_once()

    def test_poll_error_does_not_abort_reaping_and_once_retains_failure(self):
        error = RuntimeError('poll temporarily unavailable')
        _, gate, child, terminate, _, outcome = self.run_controller(
            once=True, poll_error=error)
        self.assertIs(outcome, error)
        self.assertEqual(child.waits, 1)
        terminate.assert_not_called()
        gate.release.assert_called_once()

    def test_reporting_error_cannot_turn_drain_into_worker_termination(self):
        _, gate, child, terminate, _, outcome = self.run_controller(
            once=True, step_error=RuntimeError('original step failed'), report_error=True,
            poll_error=RuntimeError('poll temporarily unavailable'))
        self.assertIsInstance(outcome, RuntimeError)
        self.assertEqual(str(outcome), 'error status failed')
        self.assertEqual(child.waits, 1)
        terminate.assert_not_called()
        gate.release.assert_called_once()

    def test_original_once_error_wins_over_later_drain_poll_error(self):
        error = RuntimeError('original step failed')
        _, gate, child, terminate, _, outcome = self.run_controller(
            once=True, step_error=error, poll_error=RuntimeError('later poll failure'))
        self.assertIs(outcome, error)
        self.assertEqual(child.waits, 1)
        terminate.assert_not_called()
        gate.release.assert_called_once()

    def test_missing_worker_or_completion_identity_never_releases_or_terminates(self):
        for missing in ('child', 'pending'):
            with self.subTest(missing=missing):
                service, gate, _, terminate, _, outcome = self.run_controller(
                    missing=missing, abort_wait=True)
                self.assertIsInstance(outcome, EndObservation)
                self.assertEqual(service.step.call_count, 1)
                terminate.assert_not_called()
                gate.release.assert_not_called()

    def test_drain_received_before_step_never_enters_queue_or_admission(self):
        service, gate, _, terminate, _, outcome = self.run_controller(drain_at_recover=True)
        self.assertIsNone(outcome)
        service.step.assert_not_called()
        terminate.assert_not_called()
        gate.release.assert_called_once()

    def test_once_without_drain_preserves_original_failure_and_cleanup(self):
        error = RuntimeError('original step failed')
        _, gate, _, terminate, _, outcome = self.run_controller(
            once=True, drain=False, step_error=error)
        self.assertIs(outcome, error)
        terminate.assert_called_once()
        gate.release.assert_called_once()

    def test_explicit_stop_without_drain_preserves_original_cleanup(self):
        _, gate, _, terminate, _, outcome = self.run_controller(drain=False, stop=True)
        self.assertIsNone(outcome)
        terminate.assert_called_once()
        gate.release.assert_called_once()

    def test_explicit_stop_interrupts_drain_wait_and_keeps_original_stop_cleanup(self):
        _, gate, child, terminate, _, outcome = self.run_controller(stop_during_wait=True)
        self.assertIsNone(outcome)
        self.assertEqual(child.waits, 0)
        terminate.assert_called_once()
        gate.release.assert_called_once()


if __name__ == '__main__':
    unittest.main()
