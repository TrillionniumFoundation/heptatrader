"""Control recovery keeps verified drivers but does not load entry-only profile data."""
from contextlib import ExitStack, redirect_stderr
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock
import heptatrader_attributed_driver as attributed
import heptatrader_btc_economics_driver as btc


class ControlOnlyLauncherTests(unittest.TestCase):
    def test_direct_control_modes_ignore_missing_or_broken_entry_profile(self):
        for mode in ('--reconcile-only', '--recover-owner'):
            for profile_args in ([], ['--profile', '/fixture/broken.json']):
                with self.subTest(mode=mode, profile_args=profile_args), ExitStack() as stack:
                    seen = []
                    driver = SimpleNamespace(main=lambda: seen.append(list(sys.argv)) or 9)
                    stack.enter_context(mock.patch.object(sys, 'argv', ['btc', '--base-driver', '/fixture/base.py', mode, *profile_args]))
                    stack.enter_context(mock.patch.object(btc.os, 'umask'))
                    load = stack.enter_context(mock.patch.object(btc, 'load_base', return_value=driver))
                    profile = stack.enter_context(mock.patch.object(btc, 'load_profile', side_effect=ValueError('entry-only profile unavailable')))
                    attach = stack.enter_context(mock.patch.object(btc, 'attach'))
                    self.assertEqual(btc.main(), 9)
                    load.assert_called_once_with(Path('/fixture/base.py'))
                    profile.assert_not_called()
                    attach.assert_not_called()
                    self.assertEqual(seen, [['/fixture/base.py', '--instrument', 'BTC.USD', mode]])

    def test_attributed_btc_control_modes_do_not_read_or_attach_profile(self):
        for mode in ('--reconcile-only', '--recover-owner'):
            with self.subTest(mode=mode), ExitStack() as stack:
                seen = []
                driver = SimpleNamespace(main=lambda: seen.append(list(sys.argv)) or 5)
                manifest = {'instrument': 'BTC.USD', 'journal_directory': '/fixture/journal',
                            'runtime_sha256': {'heptatrader_paper_driver.py': btc.BASE_SHA256}}
                stack.enter_context(mock.patch.object(sys, 'argv', ['attributed', '--manifest', '/fixture/manifest.json', mode]))
                stack.enter_context(mock.patch.object(attributed.os, 'umask'))
                manifest_loader = stack.enter_context(mock.patch.object(attributed, 'load_manifest', return_value=(manifest, 'b' * 64)))
                load = stack.enter_context(mock.patch.object(attributed, 'load_verified_module', return_value=driver))
                read = stack.enter_context(mock.patch.object(attributed, 'read_private', side_effect=FileNotFoundError('profile absent')))
                profile = stack.enter_context(mock.patch.object(btc, 'load_profile'))
                attach = stack.enter_context(mock.patch.object(btc, 'attach'))
                stack.enter_context(mock.patch.object(attributed, 'Journal', side_effect=OSError('optional journal unavailable')))
                stack.enter_context(redirect_stderr(io.StringIO()))
                self.assertEqual(attributed.main(), 5)
                base = Path(attributed.__file__).resolve().parent / 'heptatrader_paper_driver.py'
                manifest_loader.assert_called_once_with(Path('/fixture/manifest.json'))
                load.assert_called_once_with('hepta_attributed_pinned_base', base, btc.BASE_SHA256)
                read.assert_not_called()
                profile.assert_not_called()
                attach.assert_not_called()
                self.assertEqual(seen, [[str(base), '--instrument', 'BTC.USD', mode]])

    def test_entry_capable_mode_still_requires_profile(self):
        for flags in ([], ['--once']):
            with self.subTest(flags=flags), ExitStack() as stack:
                stack.enter_context(mock.patch.object(sys, 'argv', ['btc', '--base-driver', '/fixture/base.py', *flags]))
                load = stack.enter_context(mock.patch.object(btc, 'load_base'))
                stack.enter_context(redirect_stderr(io.StringIO()))
                with self.assertRaises(SystemExit) as rejected:
                    btc.main()
                self.assertEqual(rejected.exception.code, 2)
                load.assert_not_called()

    def test_entry_capable_mode_validates_and_closes_adapter(self):
        driver = SimpleNamespace(main=mock.Mock(return_value=3))
        config = {'fixture': True}
        close = mock.Mock()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, 'argv', ['btc', '--base-driver', '/fixture/base.py', '--profile', '/fixture/profile.json', '--once']))
            stack.enter_context(mock.patch.object(btc.os, 'umask'))
            stack.enter_context(mock.patch.object(btc, 'load_base', return_value=driver))
            profile = stack.enter_context(mock.patch.object(btc, 'load_profile', return_value=config))
            attach = stack.enter_context(mock.patch.object(btc, 'attach', return_value=close))
            self.assertEqual(btc.main(), 3)
            profile.assert_called_once_with(Path('/fixture/profile.json'))
            attach.assert_called_once_with(driver, config)
            close.assert_called_once_with()

    def test_broken_profile_still_blocks_entry_capable_mode(self):
        driver = SimpleNamespace(main=mock.Mock())
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, 'argv', ['btc', '--base-driver', '/fixture/base.py', '--profile', '/fixture/broken.json']))
            stack.enter_context(mock.patch.object(btc.os, 'umask'))
            stack.enter_context(mock.patch.object(btc, 'load_base', return_value=driver))
            stack.enter_context(mock.patch.object(btc, 'load_profile', side_effect=ValueError('broken profile')))
            attach = stack.enter_context(mock.patch.object(btc, 'attach'))
            with self.assertRaisesRegex(ValueError, 'broken profile'):
                btc.main()
            attach.assert_not_called()
            driver.main.assert_not_called()


if __name__ == '__main__':
    unittest.main()
