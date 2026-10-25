"""Stdlib worker-exit fixtures; only bounded synthetic children, never Torch."""
import io
import fcntl
import json
import os
from pathlib import Path
import signal
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import resident_evaluate, resident
from laya_cell.integrity import digest
from laya_cell.promotion import RULE


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


class TrainerWorkerTerminationTests(unittest.TestCase):
    def test_worker_retains_both_locks_but_native_exec_helpers_do_not(self):
        with tempfile.TemporaryDirectory() as td:
            paths=[Path(td)/name for name in ('heavy.lock','scheduler.lock')]
            held=[os.open(path,os.O_RDWR|os.O_CREAT|os.O_CLOEXEC,0o600) for path in paths]
            probes=[os.open(path,os.O_RDWR|os.O_CLOEXEC) for path in paths]
            worker=None
            try:
                for fd in held:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                service=object.__new__(resident.Supervisor)
                service._worker_lease_fd,service._controller_lock_fd=held
                inherited=resident.inherited_worker_locks(service)
                self.assertEqual(inherited,tuple(held))
                source=('import json,os,subprocess,sys,time\n'
                    'from laya_cell.resident_launch import retain_worker_locks\n'
                    'fds=[int(x) for x in sys.argv[1:]]\n'
                    'retain_worker_locks(fds)\n'
                    'probe="import os,sys; " + "; ".join("os.fstat(%s)"%fd for fd in fds)\n'
                    'result=subprocess.run([sys.executable,"-c",probe],close_fds=False,capture_output=True)\n'
                    'print(json.dumps({"cloexec":all(not os.get_inheritable(fd) for fd in fds),'
                    '"exec_inherited":result.returncode==0}),flush=True)\n'
                    'time.sleep(30)\n')
                worker=subprocess.Popen([sys.executable,'-c',source,*map(str,inherited)],
                    pass_fds=inherited,stdout=subprocess.PIPE,text=True)
                self.assertTrue(select.select([worker.stdout],[],[],5)[0],'worker lock handshake timed out')
                self.assertEqual(json.loads(worker.stdout.readline()),{'cloexec':True,'exec_inherited':False})
                for fd in held:os.close(fd)
                held=[]
                for fd in probes:
                    with self.assertRaises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                worker.terminate();worker.wait(timeout=5)
                for fd in probes:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:
                if worker is not None:
                    if worker.poll() is None:worker.kill();worker.wait(timeout=5)
                    worker.stdout.close()
                for fd in held+probes:os.close(fd)

    def test_ignored_sigterm_escalates_to_kill_and_is_reaped(self):
        process=subprocess.Popen([sys.executable,'-c',
            'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("ready",flush=True); time.sleep(30)'],
            stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'ready')
            with patch.object(resident,'WORKER_TERMINATE_GRACE_SECONDS',0.02), \
                    patch.object(resident,'WORKER_KILL_GRACE_SECONDS',2):
                code=resident.terminate_worker(process)
            self.assertEqual(code,-signal.SIGKILL)
            self.assertEqual(process.poll(),code)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            process.stdout.close()

    def test_unreapable_child_retains_identity_until_later_exact_completion(self):
        class DelayedChild:
            pid=123
            code=None
            terminated=False
            killed=False
            def poll(self):return self.code
            def terminate(self):self.terminated=True
            def kill(self):self.killed=True
            def wait(self,timeout):
                if self.code is None:raise subprocess.TimeoutExpired('fixture',timeout)
                return self.code
        child=DelayedChild()
        self.assertIsNone(resident.terminate_worker(child))
        self.assertTrue(child.terminated);self.assertTrue(child.killed)
        engine=object.__new__(resident.Supervisor);engine.child=child
        engine._pending_worker_completion={'job_id':'original-job','state':{'mature_rows':32},
                                          'failure':{'reason':'WORKER_BUDGET_EXCEEDED'}}
        engine.write_status=lambda **value:value
        completed=[]
        engine._complete_worker=lambda *args,**kw:completed.append((args,kw)) or {'state':'FAILED'}
        self.assertEqual(engine.reap_pending_worker()['state'],'WORKER_TERMINATION_PENDING')
        self.assertIs(engine.child,child);self.assertFalse(completed)
        child.code=-signal.SIGKILL
        self.assertEqual(engine.reap_pending_worker()['state'],'FAILED')
        self.assertEqual(completed[0][0],('original-job',-signal.SIGKILL,{'mature_rows':32}))
        self.assertIsNone(engine.child);self.assertIsNone(engine._pending_worker_completion)
        self.assertIsNone(engine.reap_pending_worker());self.assertEqual(len(completed),1)


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

    def test_predictor_launch_inherits_resource_and_recovery_locks(self):
        engine=object.__new__(resident_evaluate.Evaluator)
        engine.c={'python':sys.executable};engine.path=self.root/'config.json';engine.tasks={}
        engine._worker_lease_fd=40;engine._controller_lock_fd=41
        process=FakeProcess(0)
        plan={'rule':RULE,'start_ms':1000000}
        plan['plan_id']=digest(plan);self.plan.write_text(json.dumps(plan))
        with patch.object(resident_evaluate.subprocess,'Popen',return_value=process) as launch, \
                patch.object(resident_evaluate.time,'time',return_value=1000.):
            engine.launch(self.plan,0)
        try:
            args,kw=launch.call_args
            self.assertEqual(kw['pass_fds'],(40,41))
            self.assertEqual(args[0][-4:],['--held-lock-fd','40','--held-lock-fd','41'])
            self.assertEqual(len(engine.tasks),1)
        finally:
            for _,stream,_ in engine.tasks.values():stream.close()

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
