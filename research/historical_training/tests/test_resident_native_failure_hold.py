"""Native crash containment uses isolated real children, never a GPU or broker."""
import io
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from historical_training.resident_resource_guard import (
    Gate, Lease, guarded_evaluator_class, guarded_trainer_class)
from historical_training.common import EvidenceError
from laya_cell.resident_evaluate import Evaluator


class NativeFailureHoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hold = self.root/'manual-hold.json'
        self.config = {'state_root': str(self.root/'guard'),
                       'manual_hold_path': str(self.hold)}

    def test_fatal_signals_hold_new_work_without_claiming_trial_results(self):
        gate = Gate(self.config, 'trainer')
        for code in (-signal.SIGSEGV, -signal.SIGBUS, -signal.SIGABRT, -signal.SIGILL):
            with self.subTest(code=code):
                record = gate.record_native_exit(code, {'job_id': str(-code)})
                self.assertEqual(record['exit_code'], code)
                self.assertFalse(record['automatic_trading'])
                self.assertFalse(record['original_trial_assessment_changed'])
        value = json.loads(self.hold.read_text())
        self.assertEqual(value['reason'], 'NATIVE_WORKER_FATAL_SIGNAL')
        self.assertEqual(value['evidence']['signal'], 'SIGSEGV')
        self.assertEqual(value['scope'],
                         'NEW_HEAVY_GPU_WORK_ONLY_ORIGINAL_CPU_CLOSEOUT_CONTINUES')
        self.assertEqual(len(list((gate.root/'native-failures').glob('*.json'))), 4)
        self.assertTrue(gate.status_diagnostics()['manual_hold']['present'])

    def test_normal_errors_and_supervisor_kill_do_not_become_native_faults(self):
        gate = Gate(self.config, 'evaluator')
        for code in (None, 0, 1, 124, -signal.SIGTERM, -signal.SIGKILL, True):
            self.assertIsNone(gate.record_native_exit(code, {'plan': 'plan', 'slot': 0}))
        self.assertFalse(self.hold.exists())
        self.assertFalse((gate.root/'native-failures').exists())

    def test_existing_owner_marker_bytes_and_inode_are_preserved(self):
        self.hold.write_bytes(b'{"owner":"original","reason":"operator"}\n')
        original = (self.hold.read_bytes(), self.hold.stat().st_ino)
        gate = Gate(self.config, 'trainer')
        record = gate.record_native_exit(-signal.SIGSEGV, {'job_id': 'job'})
        self.assertEqual((self.hold.read_bytes(), self.hold.stat().st_ino), original)
        self.assertEqual(gate.record_native_exit(-signal.SIGSEGV, {'job_id': 'job'}), record)
        self.assertEqual(len(list((gate.root/'native-failures').glob('*.json'))), 1)

    def test_failure_to_persist_drains_controller_instead_of_retrying(self):
        gate = Gate(self.config, 'trainer')
        with patch('historical_training.resident_resource_guard.write_once',
                   side_effect=OSError('no space')):
            with self.assertRaises(OSError):
                gate.record_native_exit(-signal.SIGSEGV, {'job_id': 'job'})
        self.assertTrue(gate.draining)

    def test_trainer_records_hold_before_original_completion(self):
        gate = Gate(self.config, 'trainer')
        observed = []
        hold = self.hold
        class Base:
            def _complete_worker(self, job_id, rc, state, *, failure=None):
                observed.append(hold.exists())
                return {'job_id': job_id, 'rc': rc, 'state': state, 'failure': failure}
        engine = guarded_trainer_class(Base, gate)()
        report = engine._complete_worker('job', -signal.SIGSEGV, {'state': 'FAILED'})
        self.assertEqual(observed, [True])
        self.assertEqual(report['rc'], -signal.SIGSEGV)

    def test_actual_sigsegv_reaped_and_lease_released_with_fault_hold(self):
        gate = Gate(self.config, 'evaluator')
        engine = object.__new__(guarded_evaluator_class(Evaluator, gate))
        plan = self.root/'plan.json'
        log = self.root/'prediction-000.log'
        (self.root/'phase-000.json').write_text(json.dumps({'phase': 'VALIDATE_AND_LOAD'}))
        other = Lease(gate.root/'heavy-worker.lock')
        worker = None
        stream = log.open('w')
        try:
            self.assertTrue(gate.lease.acquire())
            worker = subprocess.Popen([sys.executable, '-c',
                'import faulthandler,os,resource,signal; '
                'resource.setrlimit(resource.RLIMIT_CORE,(0,0)); '
                'faulthandler.enable(all_threads=True); '
                'os.kill(os.getpid(),signal.SIGSEGV)'],
                pass_fds=(gate.lease.fd,), stdout=stream, stderr=stream)
            self.assertFalse(other.acquire())
            worker.wait(timeout=5)
            engine.tasks = {(str(plan), 0): (worker, stream, time.monotonic())}
            engine.reap()
            self.assertFalse(engine.tasks)
            self.assertEqual(worker.returncode, -signal.SIGSEGV)
            self.assertTrue(stream.closed)
            failure = json.loads((self.root/'failure-000.json').read_text())
            self.assertEqual(failure['reason'], 'WORKER_SIGNAL_SIGSEGV')
            self.assertIn('Fatal Python error', log.read_text())
            self.assertTrue(self.hold.exists())
            self.assertTrue(other.acquire())
            self.assertFalse((Path('/proc')/str(worker.pid)).exists())
        finally:
            if worker is not None and worker.poll() is None:
                worker.kill()
                worker.wait(timeout=5)
            stream.close()
            other.release()
            gate.release()

    def test_trainer_original_completion_survives_a_hold_write_failure(self):
        gate = Gate(self.config, 'trainer')
        completed = []
        class Base:
            def _complete_worker(self, job_id, rc, state, *, failure=None):
                completed.append((job_id, rc))
                return {'state': 'FAILED'}
        engine = guarded_trainer_class(Base, gate)()
        with patch('historical_training.resident_resource_guard.write_once',
                   side_effect=OSError('no space')):
            with self.assertRaises(OSError):
                engine._complete_worker('job', -signal.SIGSEGV, {})
        self.assertEqual(completed, [('job', -signal.SIGSEGV)])
        self.assertTrue(gate.draining)

    def test_published_prediction_is_kept_when_exit_after_publication_is_fatal(self):
        gate = Gate(self.config, 'evaluator')
        engine = object.__new__(guarded_evaluator_class(Evaluator, gate))
        plan = self.root/'plan.json'
        prediction = self.root/'prediction-000.json'
        prediction.write_bytes(b'{"original_prediction":"unchanged"}\n')
        class Child:
            def poll(self): return -signal.SIGSEGV
            def wait(self, timeout): return -signal.SIGSEGV
        stream = io.StringIO()
        engine.tasks = {(str(plan), 0): (Child(), stream, time.monotonic())}
        engine.reap()
        self.assertEqual(prediction.read_bytes(), b'{"original_prediction":"unchanged"}\n')
        self.assertFalse((self.root/'failure-000.json').exists())
        self.assertTrue(self.hold.exists())
        self.assertFalse(engine.tasks)


if __name__ == '__main__':
    unittest.main()
