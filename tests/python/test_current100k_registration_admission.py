"""Actual pidfd/proc binding; only short-lived private child processes."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import activate_prepared_driver as driver
import precheck_current100k_registration as precheck


class RegistrationAdmissionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve(); self.manifest = self.root / 'manifest.json'
        self.manifest.write_bytes(b'{"fixture":true}\n')
        self.runtime = self.root / 'driver.py'; self.runtime.write_bytes(b'# private fixture\n')
        argv = [sys.executable, '-c', "from pathlib import Path; import time; print(Path('/proc/self').resolve(), flush=True); time.sleep(30)"]
        self.child = subprocess.Popen(argv, env=dict(os.environ, INVOCATION_ID='fixture-invocation'),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        def reap():
            if self.child.poll() is None: self.child.terminate()
            self.child.wait(timeout=5)
            self.child.stdout.close()
        self.addCleanup(reap)
        # This CI container mounts the host proc namespace while subprocess
        # PIDs are local. Alias only this fixture child's self-reported proc
        # directory; pidfd_open still targets the real local child.
        proc_path = Path(self.child.stdout.readline().strip())
        self.assertEqual(proc_path.parent, Path('/proc')); self.assertTrue(proc_path.name.isdigit())
        proc_root = self.root / 'proc'; proc_root.mkdir()
        (proc_root / str(self.child.pid)).symlink_to(proc_path, target_is_directory=True)
        replace_proc = patch.object(driver, 'PROC_ROOT', proc_root)
        replace_proc.start(); self.addCleanup(replace_proc.stop)
        # Same process parsing and starttick checks as production; the argv
        # fixture substitutes only the original executable's command shape.
        replace = patch.object(driver, 'argv_for', return_value=argv); replace.start(); self.addCleanup(replace.stop)
        manifest_sha = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        self.config = {'producer_registration': {'manifest': str(self.manifest), 'sha256': manifest_sha}}
        self.request = {'registration_not_before_ms': 1}
        self.now = int(time.time() * 1000)
        self.proof = {'producer': {'manifest': str(self.manifest), 'manifest_sha256': manifest_sha,
            'release': str(self.root), 'pid_key': list(driver.pid_key(self.child.pid)),
            'invocation_id': 'fixture-invocation',
            'runtime_sha256': {'driver.py': hashlib.sha256(self.runtime.read_bytes()).hexdigest()},
            'request_activity': {'started_at_ms': self.now}}}

    def bind(self, deadline=None):
        return precheck.OriginalProducerAdmission(self.config, self.request, self.proof, deadline)

    def test_pidfd_is_held_and_only_fixed_read_paths_are_needed(self):
        with self.bind() as admission:
            descriptor = admission.pidfd
            admission(); admission()
            self.assertEqual(admission.read_only_paths, [str(self.root),
                *[f'/proc/{self.child.pid}/{name}' for name in ('stat', 'cmdline')]])
            self.assertEqual(admission.producer['request_activity']['started_at_ms'], self.now)
            os.fstat(descriptor)
        with self.assertRaises(OSError): os.fstat(descriptor)

    def test_exited_original_process_is_not_replaced_by_a_new_observation(self):
        with self.bind() as admission:
            self.child.terminate(); self.child.wait(timeout=5)
            with self.assertRaisesRegex(ValueError, 'PRODUCER_EXITED_OR_CHANGED'): admission()

    def test_sixty_second_boundary_and_wake_deadline_are_rechecked(self):
        with self.bind(self.now + 100000) as admission:
            with patch.object(precheck.time, 'time', return_value=(self.now + 60000) / 1000):
                with self.assertRaisesRegex(ValueError, 'REQUEST_EXPIRED_BEFORE_PUBLICATION'): admission()
        with self.bind(self.now + 1000) as admission:
            with patch.object(precheck.time, 'time', return_value=(self.now + 1001) / 1000):
                with self.assertRaisesRegex(ValueError, 'WAKE_DEADLINE_EXPIRED'): admission()

    def test_clock_rollback_rechecks_original_not_before(self):
        self.request['registration_not_before_ms'] = self.now - 1000
        with self.bind() as admission:
            with patch.object(precheck.time, 'time', return_value=(self.now - 1001) / 1000):
                with self.assertRaisesRegex(ValueError, 'WAIT_PRIOR_OUTCOME_END'): admission()

    def test_runtime_and_manifest_bytes_are_rechecked_inside_callback(self):
        for path, reason in ((self.runtime, 'RUNTIME_CHANGED'), (self.manifest, 'MANIFEST_CHANGED')):
            before = path.read_bytes()
            with self.bind() as admission:
                path.write_bytes(before + b'changed')
                with self.assertRaisesRegex(ValueError, reason): admission()
                path.write_bytes(before)

    def test_same_pid_with_different_invocation_or_argv_cannot_pass(self):
        real = driver.proc_bytes
        with patch.object(driver, 'proc_bytes', side_effect=lambda pid, name: b'INVOCATION_ID=other\0' if name == 'environ' else real(pid, name)):
            with self.assertRaisesRegex(ValueError, 'INVOCATION_CHANGED'): self.bind()
        with self.bind() as admission:
            with patch.object(driver, 'proc_bytes', side_effect=lambda pid, name: b'other\0' if name == 'cmdline' else real(pid, name)):
                with self.assertRaisesRegex(ValueError, 'ARGV_CHANGED'): admission()

    def test_sandbox_callback_does_not_require_privileged_environ_reread(self):
        real = driver.proc_bytes
        def confined(pid, name):
            if name == 'environ': raise PermissionError(13, 'actual sandbox proc boundary')
            return real(pid, name)
        with self.bind() as admission:
            self.assertEqual(admission.initial_invocation_id, 'fixture-invocation')
            with patch.object(driver, 'proc_bytes', side_effect=confined): admission(); admission()


if __name__ == '__main__': unittest.main()
