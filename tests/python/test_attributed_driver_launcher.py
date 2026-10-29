"""Recovery flags reach the original driver through verified wrapper imports."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import heptatrader_attributed_driver as attributed
import heptatrader_btc_economics_driver as btc
from hepta_verified_loader import SourceHashMismatch


class RecoveryLauncherTests(unittest.TestCase):
    def test_attributed_recovery_reaches_base_even_without_optional_journal(self):
        seen = []
        driver = SimpleNamespace(main=lambda: seen.append(list(sys.argv)) or 7)
        manifest = {"instrument": "EUR.USD", "journal_directory": "/fixture/journal", "runtime_sha256": {"heptatrader_paper_driver.py": "a" * 64}}
        with mock.patch.object(sys, "argv", ["launcher", "--manifest", "/fixture/manifest.json", "--recover-owner"]), \
             mock.patch.object(attributed.os, "umask"), \
             mock.patch.object(attributed, "load_manifest", return_value=(manifest, "b" * 64)), \
             mock.patch.object(attributed, "load_verified_module", return_value=driver) as load, \
             mock.patch.object(attributed, "Journal", side_effect=OSError("fixture journal unavailable")), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(attributed.main(), 7)
        base = Path(attributed.__file__).resolve().parent / "heptatrader_paper_driver.py"
        load.assert_called_once_with("hepta_attributed_pinned_base", base, "a" * 64)
        self.assertEqual(seen, [[str(base), "--instrument", "EUR.USD", "--recover-owner"]])

    def test_direct_btc_recovery_does_not_attach_entry_profile(self):
        seen = []
        driver = SimpleNamespace(main=lambda: seen.append(list(sys.argv)) or 0)
        close = mock.Mock()
        with mock.patch.object(sys, "argv", ["btc", "--base-driver", "/fixture/base.py", "--profile", "/fixture/profile.json", "--recover-owner"]), \
             mock.patch.object(btc.os, "umask"), \
             mock.patch.object(btc, "load_profile", return_value={}), \
             mock.patch.object(btc, "load_base", return_value=driver), \
             mock.patch.object(btc, "attach", return_value=close):
            self.assertEqual(btc.main(), 0)
        self.assertEqual(seen, [["/fixture/base.py", "--instrument", "BTC.USD", "--recover-owner"]])
        close.assert_not_called()

    def test_failed_pinned_import_never_reaches_optional_journal_or_driver(self):
        manifest = {"instrument": "EUR.USD", "journal_directory": "/fixture/journal", "runtime_sha256": {"heptatrader_paper_driver.py": "a" * 64}}
        with mock.patch.object(sys, "argv", ["launcher", "--manifest", "/fixture/manifest.json", "--recover-owner"]), \
             mock.patch.object(attributed.os, "umask"), \
             mock.patch.object(attributed, "load_manifest", return_value=(manifest, "b" * 64)), \
             mock.patch.object(attributed, "load_verified_module", side_effect=SourceHashMismatch("fixture mismatch")), \
             mock.patch.object(attributed, "Journal") as journal:
            with self.assertRaisesRegex(SourceHashMismatch, "fixture mismatch"):
                attributed.main()
        journal.assert_not_called()


class ManifestBaseBindingTests(unittest.TestCase):
    def setUp(self):
        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        self.root = Path(box.name)
        source = Path(attributed.__file__).resolve().parent
        self.base = self.root / 'heptatrader_paper_driver.py'
        self.base.write_text('def main():\n    return 27\n')
        self.base.chmod(0o600)
        hashes = {self.base.name: self.sha(self.base)}
        for name in ('heptatrader_btc_economics_driver.py', 'hepta_btc_entry_policy.py',
                     'hepta_verified_loader.py'):
            target = self.root / name
            target.write_bytes((source / name).read_bytes())
            target.chmod(0o600)
            hashes[name] = self.sha(target)
        period = self.root / 'period.json'
        self.write(period, {'execution_domain': 'PAPER'})
        profile = self.root / 'profile.json'
        self.write(profile, {'fixture': True})
        self.path = self.root / 'manifest.json'
        self.manifest = {
            'schema': 'heptatrader.attribution-producer.v1',
            'instrument': 'BTC.USD', 'execution_domain': 'PAPER',
            'runtime_loader': 'verified-bytes.v1', 'btc_base_binding': 'RUNTIME_MANIFEST_V1',
            'period_path': str(period), 'period_sha256': self.sha(period),
            'runtime_sha256': hashes, 'btc_profile_sha256': self.sha(profile),
            'journal_directory': str(self.root / 'journal'),
        }
        self.write(self.path, self.manifest)

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def launch(self):
        close = mock.Mock()
        with mock.patch.object(attributed, '__file__', str(self.root / 'launcher.py')), \
             mock.patch.object(sys, 'argv', ['launcher', '--manifest', str(self.path)]), \
             mock.patch.object(btc, 'load_profile', return_value={}), \
             mock.patch.object(btc, 'attach', return_value=close), \
             mock.patch.object(attributed, 'Journal', side_effect=OSError('optional observer')), \
             contextlib.redirect_stderr(io.StringIO()):
            result = attributed.main()
        close.assert_called_once_with()
        return result

    def test_generated_manifest_binds_new_base_without_copying_legacy_constant(self):
        self.assertNotEqual(self.sha(self.base), btc.BASE_SHA256)
        self.assertEqual(self.launch(), 27)

    def test_changed_base_under_old_manifest_cannot_execute(self):
        self.base.write_text("raise AssertionError('unverified source executed')\n")
        with self.assertRaisesRegex(ValueError, 'PINNED_RUNTIME_CHANGED'):
            self.launch()

    def test_legacy_manifest_does_not_gain_implicit_new_base_permission(self):
        self.manifest.pop('btc_base_binding')
        self.write(self.path, self.manifest)
        with self.assertRaisesRegex(ValueError, 'BTC_BASE_BINDING'):
            self.launch()

    def test_unknown_mode_or_missing_bound_adapter_rejects(self):
        self.manifest['btc_base_binding'] = 'UNVERIFIED'
        self.write(self.path, self.manifest)
        with self.assertRaisesRegex(ValueError, 'BTC_BASE_BINDING_MANIFEST_INVALID'):
            self.launch()
        self.manifest['btc_base_binding'] = 'RUNTIME_MANIFEST_V1'
        del self.manifest['runtime_sha256']['heptatrader_btc_economics_driver.py']
        self.write(self.path, self.manifest)
        with self.assertRaisesRegex(ValueError, 'BTC_BASE_BINDING_MANIFEST_INVALID'):
            self.launch()


if __name__ == "__main__":
    unittest.main()
