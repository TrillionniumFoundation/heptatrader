"""Recovery flags reach the original driver through verified wrapper imports."""
from __future__ import annotations
import contextlib
import io
from pathlib import Path
import sys
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


if __name__ == "__main__":
    unittest.main()
