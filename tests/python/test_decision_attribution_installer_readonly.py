"""Offline inspection and no-op tests: never invoke a real broker or systemd."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import install_decision_attribution as installer


class InstalledAttributionInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Keep the entry check separate from real private-file ownership.
        # A different UID exposes accidental patches to the shared os module.
        self.installer_uid = os.geteuid() + 1
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
        with patch.object(installer, 'os',
                          SimpleNamespace(geteuid=lambda: self.installer_uid)), \
             patch.object(installer, 'show', return_value=self.service), \
             patch.object(installer.subprocess, 'check_output',
                          side_effect=AssertionError('git/broker query forbidden')), \
             patch.object(installer.subprocess, 'run',
                          side_effect=AssertionError('service restart forbidden')):
            result = installer.install('EUR.USD', self.root / 'evidence')
        self.assertEqual(result['status'], 'ALREADY_CURRENT_NOOP')
        self.assertFalse((self.root / 'evidence').exists())

    def test_different_source_cannot_reinstall_into_nonflat_lane(self):
        with patch.object(installer, 'os',
                          SimpleNamespace(geteuid=lambda: self.installer_uid)), \
             patch.object(installer, 'show', return_value=self.service), \
             patch.object(installer, 'sha', return_value='0' * 64), \
             patch.object(installer.subprocess, 'run',
                          side_effect=AssertionError('service restart forbidden')):
            with self.assertRaisesRegex(RuntimeError, 'OWNER_SAFE_UPGRADE'):
                installer.install('EUR.USD', self.root / 'evidence')

    def test_inactive_resident_not_mistaken_for_running_noop(self):
        with patch.object(installer, 'os',
                          SimpleNamespace(geteuid=lambda: self.installer_uid)), \
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

    def _observed_process(self, manifest, *, argv=None, keys=None):
        import contextlib
        import activate_prepared_driver as activation
        stack = contextlib.ExitStack()
        status = dict(ActiveState="active", MainPID="4201", InvocationID="e" * 32)
        actual = activation.argv_for(manifest) if argv is None else argv
        def read_proc(pid, name):
            self.assertEqual((pid, name), (4201, "cmdline"))
            return b"\0".join(item.encode() for item in actual) + b"\0"
        stack.enter_context(patch.object(activation.fence, "show_unit", return_value=status))
        stack.enter_context(patch.object(installer, "show", return_value=self.service))
        identity = {"side_effect": keys} if keys is not None else {"return_value": ("4201", "12345")}
        stack.enter_context(patch.object(activation, "pid_key", **identity))
        stack.enter_context(patch.object(activation, "proc_bytes", side_effect=read_proc))
        stack.enter_context(patch.object(activation, "ctl", side_effect=AssertionError("inspection changed service")))
        return stack

    def _legacy_release(self, instrument="EUR.USD"):
        release = self.root / ("legacy-" + instrument)
        release.mkdir()
        hashes = {}
        for name in installer.runtime_filenames(instrument, legacy=True):
            raw = (ROOT / "scripts" / name).read_bytes()
            if name == "heptatrader_attributed_driver.py":
                raw = b"# Synthetic immutable pre-loader launcher fixture.\n"
            self._put(release / name, raw)
            hashes[name] = hashlib.sha256(raw).hexdigest()
        manifest = dict(self.manifest, instrument=instrument, source_sha="b" * 40,
                        runtime_sha256=hashes)
        manifest.pop("runtime_loader", None)
        if instrument == "BTC.USD":
            self._put(release / "profile.json", b"{}\n")
            manifest["btc_profile_sha256"] = installer.sha(release / "profile.json")
        path = release / "manifest.json"
        self._put(path, (json.dumps(manifest, sort_keys=True) + "\n").encode())
        return path

    def test_exact_legacy_layouts_remain_inspectable_and_tamper_detected(self):
        import activate_prepared_driver as activation
        for instrument in ("EUR.USD", "BTC.USD"):
            with self.subTest(instrument=instrument):
                path = self._legacy_release(instrument)
                command = "ExecStart={ argv[]=" + " ".join(activation.argv_for(path)) + " ; }"
                result = installer.inspect_resident(command, instrument)
                self.assertEqual(result["status"], "INSTALLED_VERIFIED")
                self.assertEqual(set(result["runtime_sha256"]), installer.runtime_filenames(instrument, legacy=True))
                base = path.parent / "heptatrader_paper_driver.py"
                self._put(base, base.read_bytes() + b"\n# changed\n")
                with self.assertRaisesRegex(RuntimeError, "RUNTIME_CHANGED"):
                    installer.inspect_resident(command, instrument)

    def test_new_layout_cannot_silently_drop_the_verified_loader(self):
        self.manifest["runtime_loader"] = "verified-bytes.v1"
        self.manifest["runtime_sha256"] = dict(self.hashes)
        del self.manifest["runtime_sha256"]["hepta_verified_loader.py"]
        self._manifest()
        with self.assertRaisesRegex(RuntimeError, "RUNTIME_INCOMPLETE"):
            installer.inspect_resident(self.command, "EUR.USD")

    def test_unknown_runtime_file_is_not_an_accepted_legacy_layout(self):
        self.manifest["runtime_sha256"] = dict(self.hashes, unexpected_module="0" * 64)
        self._manifest()
        with self.assertRaisesRegex(RuntimeError, "RUNTIME_INCOMPLETE"):
            installer.inspect_resident(self.command, "EUR.USD")

    def test_pre_stream_verified_layout_is_retained_but_cannot_enable_missing_reader(self):
        self.manifest['runtime_loader'] = 'verified-bytes.v1'
        self.manifest['runtime_sha256'] = dict(self.hashes)
        del self.manifest['runtime_sha256']['hepta_native_reader.py']
        self._manifest()
        result = installer.inspect_resident(self.command, 'EUR.USD')
        self.assertEqual(result['status'], 'INSTALLED_VERIFIED')
        self.assertIs(result['native_read_stream'], False)
        self.manifest['native_read_stream'] = True
        self._manifest()
        with self.assertRaisesRegex(RuntimeError, 'RUNTIME_INCOMPLETE'):
            installer.inspect_resident(self.command, 'EUR.USD')

    def test_enabled_reader_is_pinned_and_tamper_is_refused(self):
        self.manifest['native_read_stream'] = True
        self._manifest()
        result = installer.inspect_resident(self.command, 'EUR.USD')
        self.assertIs(result['native_read_stream'], True)
        reader = self.release / 'hepta_native_reader.py'
        self._put(reader, reader.read_bytes() + b'\n# changed reader\n')
        with self.assertRaisesRegex(RuntimeError, 'RUNTIME_CHANGED:hepta_native_reader'):
            installer.inspect_resident(self.command, 'EUR.USD')

    def test_configured_and_running_revisions_are_independent(self):
        import activate_prepared_driver as activation
        actual = self._legacy_release()
        with self._observed_process(actual):
            result = installer.inspect_running_runtime(installer.unit_for("EUR.USD"), "EUR.USD")
            with self.assertRaisesRegex(RuntimeError, "RUNNING_DRIVER_ARGV_MISMATCH"):
                activation.running_driver(installer.unit_for("EUR.USD"), "EUR.USD")
        self.assertEqual(result["status"], "CONFIGURED_DIFFERS_FROM_RUNNING")
        self.assertFalse(result["configured_matches_running"])
        self.assertEqual(result["configured"]["source_sha"], "a" * 40)
        self.assertEqual(result["running"]["source_sha"], "b" * 40)
        self.assertEqual(result["running"]["manifest"], str(actual))
        self.assertEqual(result["running"]["pid_key"], ("4201", "12345"))
        self.assertFalse(result["service_changed"])

    def test_bad_configuration_does_not_hide_valid_running_legacy(self):
        actual = self._legacy_release()
        base = self.release / "heptatrader_paper_driver.py"
        self._put(base, base.read_bytes() + b"\n# configuration tamper\n")
        with self._observed_process(actual):
            result = installer.inspect_running_runtime(installer.unit_for("EUR.USD"), "EUR.USD")
        self.assertEqual(result["configured"]["status"], "UNVERIFIED")
        self.assertEqual(result["running"]["status"], "RUNNING_VERIFIED")
        self.assertEqual(result["running"]["source_sha"], "b" * 40)
        self.assertIsNone(result["configured_matches_running"])

    def test_extra_process_arguments_are_not_reported_as_pinned_runtime(self):
        import activate_prepared_driver as activation
        argv = activation.argv_for(self.manifest_path) + ["--unexpected"]
        with self._observed_process(self.manifest_path, argv=argv):
            result = installer.inspect_running_runtime(installer.unit_for("EUR.USD"), "EUR.USD")
        self.assertEqual(result["running"]["status"], "UNVERIFIED")
        self.assertIn("ARGV_MISMATCH", result["running"]["error"])
        self.assertNotIn("source_sha", result["running"])

    def test_pid_reuse_during_inspection_is_an_explicit_error(self):
        with self._observed_process(self.manifest_path, keys=[("4201", "12345"), ("4201", "67890")]):
            result = installer.inspect_running_runtime(installer.unit_for("EUR.USD"), "EUR.USD")
        self.assertEqual(result["running"]["status"], "UNVERIFIED")
        self.assertIn("DRIVER_CHANGED_DURING_INSPECTION", result["running"]["error"])
        self.assertIsNone(result["configured_matches_running"])


if __name__ == '__main__':
    unittest.main()
