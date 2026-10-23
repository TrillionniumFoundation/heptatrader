"""Pure stdlib worker-exit fixtures; never launch a process or import Torch."""
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import resident_evaluate


class FakeProcess:
    def __init__(self, returncode, *, running=False):
        self.returncode = returncode
        self.running = running
        self.terminated = False
        self.wait_timeouts = []

    def poll(self):
        return None if self.running else self.returncode

    def terminate(self):
        self.terminated = True
        self.running = False

    def wait(self, timeout):
        self.wait_timeouts.append(timeout)
        return self.returncode


class WorkerFailureDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.plan = self.root/'plan.json'
        self.log = self.root/'prediction-000.log'
        self.failure = self.root/'failure-000.json'

    def run_reap(self, returncode, log=b'', *, expired=False, prediction=False,
                 running=False, preserve_log=False):
        if not preserve_log:
            self.log.write_bytes(log)
        if prediction:
            (self.root/'prediction-000.json').write_text('{}')
        (self.root/'phase-000.json').write_text(json.dumps({'phase':'VALIDATE_AND_LOAD'}))
        engine = object.__new__(resident_evaluate.Evaluator)
        process = FakeProcess(returncode, running=running)
        stream = io.StringIO()
        engine.tasks = {(str(self.plan),0):(process,stream,0. if expired else 999.)}
        with patch.object(resident_evaluate.time, 'monotonic', return_value=1000.), \
                patch.object(resident_evaluate.subprocess, 'Popen', side_effect=AssertionError('No worker launch')):
            engine.reap()
        record = json.loads(self.failure.read_bytes()) if self.failure.exists() else None
        return record, process, stream, engine

    def test_sigsegv_is_reason_and_common_warning_is_separate(self):
        warning = b'rocSHMEM Could not open libnuma. Returning\n'
        record, _, stream, engine = self.run_reap(-signal.SIGSEGV, warning)
        self.assertEqual(record['reason'], 'WORKER_SIGNAL_SIGSEGV')
        self.assertEqual(record['exit_code'], -signal.SIGSEGV)
        self.assertEqual(record['stderr_tail'], warning.decode())
        self.assertEqual(record['phase'], {'phase':'VALIDATE_AND_LOAD'})
        self.assertFalse(record['predicted'])
        self.assertTrue(stream.closed)
        self.assertFalse(engine.tasks)

    def test_sigabrt_is_reason_with_empty_stderr(self):
        record, _, _, _ = self.run_reap(-signal.SIGABRT)
        self.assertEqual(record['reason'], 'WORKER_SIGNAL_SIGABRT')
        self.assertEqual(record['stderr_tail'], '')

    def test_unrecognized_signal_retains_number_without_crashing_reaper(self):
        record, _, _, _ = self.run_reap(-9999)
        self.assertEqual(record['reason'], 'WORKER_SIGNAL_9999')

    def test_budget_reason_keeps_priority_over_signal_and_stderr(self):
        record, process, _, _ = self.run_reap(-signal.SIGSEGV, b'last warning\n',
                                             expired=True, running=True)
        self.assertEqual(record['reason'], 'WORKER_BUDGET_EXCEEDED')
        self.assertEqual(record['stderr_tail'], 'last warning\n')
        self.assertTrue(process.terminated)
        self.assertEqual(process.wait_timeouts, [5])

    def test_successful_exit_with_prediction_does_not_create_failure(self):
        record, process, stream, engine = self.run_reap(0, prediction=True)
        self.assertIsNone(record)
        self.assertFalse(process.terminated)
        self.assertTrue(stream.closed)
        self.assertFalse(engine.tasks)

    def test_existing_prediction_is_preserved_even_if_process_later_signalled(self):
        record, _, _, _ = self.run_reap(-signal.SIGSEGV, prediction=True)
        self.assertIsNone(record)

    def test_positive_exit_keeps_original_last_line_reason_and_limit(self):
        last = 'x'*300
        record, _, _, _ = self.run_reap(2, ('earlier warning\n'+last+'\n').encode())
        self.assertEqual(record['reason'], last[:240])
        self.assertEqual(record['exit_code'], 2)
        self.assertTrue(record['stderr_tail'].endswith(last+'\n'))

    def test_empty_log_keeps_original_missing_diagnostic_reason(self):
        record, _, _, _ = self.run_reap(2)
        self.assertEqual(record['reason'], 'NO_PROCESS_DIAGNOSTIC')

    def test_zero_exit_without_prediction_keeps_original_missing_output_failure(self):
        record, _, _, _ = self.run_reap(0)
        self.assertEqual(record['reason'], 'NO_PROCESS_DIAGNOSTIC')
        self.assertEqual(record['exit_code'], 0)

    def test_running_worker_before_budget_is_not_reaped(self):
        record, process, stream, engine = self.run_reap(0, running=True)
        self.assertIsNone(record)
        self.assertFalse(process.terminated)
        self.assertFalse(stream.closed)
        self.assertTrue(engine.tasks)

    def test_only_last1500_bytes_are_read_and_invalid_utf8_is_replaced(self):
        tail = b'\xff'+b'z'*1498+b'\n'
        with self.log.open('wb') as stream:
            stream.seek(4*1024*1024)
            stream.write(tail)
        actual_open = Path.open
        reads = []
        seeks = []
        class BoundedLog:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *_):
                self.stream.close()
            def seek(self, offset, whence=os.SEEK_SET):
                seeks.append((offset,whence))
                return self.stream.seek(offset,whence)
            def tell(self):
                return self.stream.tell()
            def read(self, size=-1):
                if not 0 <= size <= 1500:
                    raise AssertionError('Unbounded log read')
                reads.append(size)
                return self.stream.read(size)
        log_path = self.log
        def opening(path, *args, **kwargs):
            stream = actual_open(path,*args,**kwargs)
            return BoundedLog(stream) if path == log_path else stream
        with patch.object(Path, 'open', opening), \
                patch.object(Path, 'read_text', side_effect=AssertionError('Whole log read')):
            record, _, _, _ = self.run_reap(-signal.SIGSEGV, preserve_log=True)
        self.assertEqual(reads, [1500])
        self.assertEqual(seeks, [(0,os.SEEK_END),(4*1024*1024,os.SEEK_SET)])
        self.assertEqual(record['stderr_tail'], tail.decode('utf-8',errors='replace'))
        self.assertEqual(len(record['stderr_tail']), 1500)
        self.assertEqual(record['reason'], 'WORKER_SIGNAL_SIGSEGV')


if __name__ == '__main__':
    unittest.main()
