"""Offline inspection and no-op tests: never invoke a real broker or systemd."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import install_decision_attribution as installer


class InstalledAttributionInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.period = self.root / 'period.json'
        self._put(self.period, b'{"execution_domain":"PAPER"}\n')
        self.release = self.root / 'releases' / 'identity' / 'EUR.USD'
        self.release.mkdir(parents=True, mode=0o700)
        self.hashes = {}
        for name in installer.runtime_filenames('EUR.USD'):
            raw = (ROOT / 'scripts' / name).read_bytes()
            self._put(self.release / name, raw)
            self.hashes[name] = hashlib.sha256(raw).hexdigest()
        self.manifest = dict(schema='heptatrader.attribution-producer.v1',
                             instrument='EUR.USD', execution_domain='PAPER',
                             actor_scope='EXISTING_LOCAL_DRIVER',
                             period_path=str(self.period),
                             period_sha256=hashlib.sha256(self.period.read_bytes()).hexdigest(),
                             source_sha='a' * 40, runtime_sha256=self.hashes,
                             journal_directory=str(self.root / 'journal'))
        self.manifest_path = self.release / 'manifest.json'
        self._manifest()
        self.command = ('ExecStart={ path=/usr/bin/python3 ; '
                        'argv[]=/usr/bin/python3 -B '
                        + str(self.release / 'heptatrader_attributed_driver.py')
                        + ' --manifest ' + str(self.manifest_path) +
                        ' ; ignore_errors=no ; }')
        # Scope the non-root installer simulation to its module reference.
        # Patching os.geteuid globally also falsifies read_private ownership.
        self.service = dict(ActiveState='active', MainPID='4201',
                            ExecStart=self.command)

    def _put(self, path, content):
        path.write_bytes(content)
        path.chmod(0o600)

    def _manifest(self):
        self._put(self.manifest_path,
                  (json.dumps(self.manifest, sort_keys=True) + '\n').encode())

    def test_inspect_current_without_state_or_broker(self):
        result = installer.inspect_resident(self.command, 'EUR.USD')
        self.assertEqual(result['status'], 'INSTALLED_VERIFIED')
        self.assertFalse(result['trade_authority'])
        self.assertFalse(result['service_changed'])
        self.assertEqual(set(result['runtime_sha256']), installer.runtime_filenames('EUR.USD'))

    def test_nonflat_identical_install_is_noop_without_external_commands(self):
        # No state file exists, let alone a flat snapshot. Non-mutating
        # reinstallation must exit before any git, broker or systemd stop.
        with patch.object(installer, 'os', SimpleNamespace(geteuid=lambda: 1000)), \
             patch.object(installer, 'show', return_value=self.service), \
             patch.object(installer.subprocess, 'check_output',
                          side_effect=AssertionError('git/broker query forbidden')), \
             patch.object(installer.subprocess, 'run',
                          side_effect=AssertionError('service restart forbidden')):
            result = installer.install('EUR.USD', self.root / 'evidence')
        self.assertEqual(result['status'], 'ALREADY_CURRENT_NOOP')
        self.assertFalse((self.root / 'evidence').exists())

    def test_different_source_cannot_reinstall_into_nonflat_lane(self):
        with patch.object(installer, 'os', SimpleNamespace(geteuid=lambda: 1000)), \
             patch.object(installer, 'show', return_value=self.service), \
             patch.object(installer, 'sha', return_value='0' * 64), \
             patch.object(installer.subprocess, 'run',
                          side_effect=AssertionError('service restart forbidden')):
            with self.assertRaisesRegex(RuntimeError, 'OWNER_SAFE_UPGRADE'):
                installer.install('EUR.USD', self.root / 'evidence')

    def test_inactive_resident_not_mistaken_for_running_noop(self):
        with patch.object(installer, 'os', SimpleNamespace(geteuid=lambda: 1000)), \
             patch.object(installer, 'show', return_value={**self.service, 'ActiveState':'inactive'}):
            with self.assertRaisesRegex(RuntimeError, 'RESIDENT_NOT_ACTIVE'):
                installer.install('EUR.USD', self.root / 'evidence')

    def test_tampered_runtime_or_period_rejects(self):
        path = self.release / 'heptatrader_paper_driver.py'
        path.write_bytes(path.read_bytes() + b'\n# appended malicious change\n')
        with self.assertRaisesRegex(RuntimeError, 'RUNTIME_CHANGED'):
            installer.inspect_resident(self.command, 'EUR.USD')
        self._put(path, (ROOT / 'scripts' / path.name).read_bytes())
        self._put(self.period, b'{"execution_domain":"LIVE"}\n')
        with self.assertRaisesRegex(RuntimeError, 'PERIOD_CHANGED'):
            installer.inspect_resident(self.command, 'EUR.USD')

    def test_false_scope_retired_limiter_and_ambiguous_manifest_reject(self):
        with self.assertRaisesRegex(RuntimeError, 'SCOPE_MISMATCH'):
            installer.inspect_resident(self.command, 'BTC.USD')
        self.manifest['fx_churn_profile_sha256'] = 'z' * 64
        self._manifest()
        with self.assertRaisesRegex(RuntimeError, 'SCOPE_MISMATCH'):
            installer.inspect_resident(self.command, 'EUR.USD')
        with self.assertRaisesRegex(RuntimeError, 'IDENTITY_AMBIGUOUS'):
            installer.inspect_resident(self.command + ' --manifest ' + str(self.manifest_path), 'EUR.USD')

    def test_named_script_in_unrelated_argv_is_not_accepted(self):
        # Manifest bytes may be correct while systemd launches unrelated code.
        # A string reference to the attributed driver is not execution identity.
        unrelated = ('ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -B '
                     + str(self.root / 'other.py') + ' --manifest ' + str(self.manifest_path)
                     + ' --note heptatrader_attributed_driver.py ; }')
        with self.assertRaisesRegex(RuntimeError, 'EXECSTART_IDENTITY_MISMATCH'):
            installer.inspect_resident(unrelated, 'EUR.USD')

    def test_noninstrumented_status_not_trade_authorization(self):
        status = installer.inspect_resident('ExecStart={ argv[]=/usr/bin/python3 old-driver ; }','EUR.USD')
        self.assertEqual(status['status'], 'NOT_INSTRUMENTED')
        self.assertFalse(status['trade_authority'])


if __name__ == '__main__':
    unittest.main()
