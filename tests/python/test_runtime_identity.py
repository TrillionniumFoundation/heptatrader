"""Configured release selection never proves the native image already loaded."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hepta_runtime_identity as identity


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-runtime-identity-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.candidate = self.root / "candidate"
        self.loaded = self.root / "loaded"
        self.candidate.write_bytes(b"new native fixture")
        self.loaded.write_bytes(b"old native fixture")
        self.service = {"MainPID": "77", "ActiveState": "active", "NRestarts": "0",
                        "ExecStart": "{ path=" + str(self.candidate) + " ; argv[]=" + str(self.candidate) + " ; }"}

    def inspect(self, *, changed_pid=False, changed_config=False):
        real_hash = identity.executable_identity
        def read_process(_pid, field, _limit=65536):
            return (str(self.loaded).encode() + b"\0" if field == "cmdline" else
                    b"PYTHONPATH=/fixture/release/learn:/fixture/release/scripts\0PRIVATE_TEST_SECRET=not-for-output\0")
        final = {**self.service, "ExecStart": "changed"} if changed_config else self.service
        with patch.object(identity.attribution, "show", side_effect=[self.service, final]), \
             patch.object(identity, "configured_pythonpath", return_value=["/fixture/new/learn"]), \
             patch.object(identity.activation, "pid_key",
                          side_effect=[("77", "100"), ("77", "101" if changed_pid else "100")]), \
             patch.object(identity.activation, "proc_bytes", side_effect=read_process), \
             patch.object(identity.os, "readlink", return_value=str(self.loaded)), \
             patch.object(identity, "executable_identity",
                          side_effect=lambda path, process_link=False: real_hash(self.loaded if process_link else path)):
            return identity.inspect_unit("heptatrader-execution.service")

    def test_configured_candidate_and_loaded_old_image_remain_distinct(self):
        result = self.inspect()
        self.assertFalse(result["configured_matches_loaded_executable"])
        self.assertEqual(result["configured"]["executable"]["sha256"],
                         hashlib.sha256(self.candidate.read_bytes()).hexdigest())
        self.assertEqual(result["running"]["executable"]["sha256"],
                         hashlib.sha256(self.loaded.read_bytes()).hexdigest())
        self.assertEqual(result["running"]["start_ticks"], "100")
        self.assertEqual(result["running"]["pythonpath"], ["/fixture/release/learn", "/fixture/release/scripts"])
        self.assertNotIn("not-for-output", json.dumps(result))
        self.assertFalse(result["trade_authority"])
        self.assertFalse(result["service_changed"])

    def test_process_reuse_and_configuration_changes_are_not_a_stable_observation(self):
        for options in ({"changed_pid": True}, {"changed_config": True}):
            with self.subTest(options=options), self.assertRaisesRegex(RuntimeError, "PROCESS_CHANGED"):
                self.inspect(**options)

    def test_matching_bytes_are_reported_without_claiming_python_module_memory(self):
        self.loaded.write_bytes(self.candidate.read_bytes())
        result = self.inspect()
        self.assertTrue(result["configured_matches_loaded_executable"])
        self.assertFalse(result["python_in_memory_module_bytes_verified"])




class AggregateRuntimeIdentityTests(unittest.TestCase):
    def _probe_records(self):
        return {
            unit: {"unit": unit, "status": "FIXTURE_PROBE_RESULT",
                   "probe_payload": {"configured_sha256": "a" * 64,
                                     "loaded_sha256": "b" * 64,
                                     "pid": 7000 + index}}
            for index, unit in enumerate(identity.UNITS)
        }
    def test_all_units_keep_generic_identity_and_only_drivers_get_release_identity(self):
        paper = "heptatrader-paper-driver.service"
        crypto = "heptatrader-crypto-driver.service"
        probes = self._probe_records()
        releases = {
            paper: {
                "status": "CONFIGURED_DIFFERS_FROM_RUNNING",
                "configured": {"source_sha": "c" * 40, "manifest_sha256": "1" * 64},
                "running": {"source_sha": "d" * 40, "manifest_sha256": "2" * 64},
                "configured_matches_running": False,
            },
            crypto: {
                "status": "CONFIGURED_AND_RUNNING_VERIFIED",
                "configured": {"source_sha": "e" * 40, "manifest_sha256": "3" * 64},
                "running": {"source_sha": "e" * 40, "manifest_sha256": "3" * 64},
                "configured_matches_running": True,
            },
        }
        with patch.object(identity, "inspect_unit", side_effect=lambda unit: dict(probes[unit])) as probe, \
             patch.object(identity.attribution, "inspect_running_runtime",
                          side_effect=lambda unit, instrument: releases[unit]) as release:
            result = identity.inspect()
        self.assertEqual(set(result["units"]), set(identity.UNITS))
        self.assertEqual(probe.call_count, len(identity.UNITS))
        self.assertCountEqual([item.args for item in probe.call_args_list], [(unit,) for unit in identity.UNITS])
        self.assertCountEqual([(item.args, item.kwargs) for item in release.call_args_list],
                              [((paper, "EUR.USD"), {}), ((crypto, "BTC.USD"), {})])
        for unit, expected in probes.items():
            observed = result["units"][unit]
            self.assertEqual({key: observed[key] for key in expected}, expected)
            if unit in releases:self.assertEqual(observed["driver_release"], releases[unit])
            else:self.assertNotIn("driver_release", observed)
        self.assertIs(result["units"][paper]["driver_release"]["configured_matches_running"], False)
        self.assertFalse(result["trade_authority"]);self.assertFalse(result["service_changed"])
    def test_one_driver_release_error_preserves_the_other_units_and_native_evidence(self):
        paper = "heptatrader-paper-driver.service";crypto = "heptatrader-crypto-driver.service"
        probes = self._probe_records()
        crypto_release = {
            "status": "CONFIGURED_AND_RUNNING_VERIFIED",
            "configured": {"source_sha": "e" * 40, "manifest_sha256": "3" * 64},
            "running": {"source_sha": "e" * 40, "manifest_sha256": "3" * 64},
            "configured_matches_running": True,
        }
        def release_probe(unit, instrument):
            if unit == paper:raise RuntimeError("fixture running manifest unavailable")
            self.assertEqual((unit, instrument), (crypto, "BTC.USD"))
            return crypto_release
        with patch.object(identity, "inspect_unit", side_effect=lambda unit: dict(probes[unit])), \
             patch.object(identity.attribution, "inspect_running_runtime", side_effect=release_probe) as release:
            result = identity.inspect()
        self.assertEqual(set(result["units"]), set(identity.UNITS));self.assertEqual(release.call_count, 2)
        for unit, expected in probes.items():
            observed=result["units"][unit]
            self.assertEqual({key: observed[key] for key in expected}, expected)
            if unit not in (paper, crypto):self.assertNotIn("driver_release", observed)
        failed=result["units"][paper]["driver_release"]
        self.assertEqual(failed["status"], "INSPECTION_INCOMPLETE")
        self.assertIn("fixture running manifest unavailable", failed["error"])
        self.assertIsNone(failed["configured_matches_running"]);self.assertNotIn("running", failed)
        self.assertFalse(failed["trade_authority"]);self.assertFalse(failed["service_changed"])
        self.assertEqual(result["units"][crypto]["driver_release"], crypto_release)
        self.assertFalse(result["trade_authority"]);self.assertFalse(result["service_changed"])


class ConfigurationIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hepta-config-identity-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.original = self.home / "resident.json"
        self.original.write_text(json.dumps({
            "schema": "heptatrader.laya-resident-config.v1",
            "runtime_directory": "/fixture/resident-release",
            "unrelated_secret": "fixture-private-secret",
        }))
        self.original.chmod(0o400)
        self.guard = self.home / ".local/share/heptatrader/resident-resource-guard/releases/fixture/configs/trainer.json"
        self.guard.parent.mkdir(parents=True)
        self.payload = {
            "schema": "heptatrader.resident-admission-wrapper-config.v1",
            "trade_authority": False, "original_config": str(self.original),
            "original_config_sha256": hashlib.sha256(self.original.read_bytes()).hexdigest(),
        }
        self.write_guard(self.payload)

    def write_guard(self, payload, mode=0o444):
        if self.guard.exists():self.guard.chmod(0o600)
        self.guard.write_text(json.dumps(payload))
        self.guard.chmod(mode)

    def inspect(self, path=None):
        with patch.object(identity.Path, "home", return_value=self.home):
            return identity.configuration_identity(["python", "--config", str(path or self.guard)])

    def test_installed_read_only_guard_reveals_the_pinned_private_runtime(self):
        result = self.inspect()
        self.assertEqual(result["sha256"], hashlib.sha256(self.guard.read_bytes()).hexdigest())
        original = result["original_configuration"]
        self.assertTrue(original["guard_pin_matches"])
        self.assertEqual(original["runtime_directory"], "/fixture/resident-release")
        self.assertEqual(self.inspect(self.original)["runtime_directory"], "/fixture/resident-release")
        self.assertNotIn("fixture-private-secret", json.dumps(result))
        self.assertEqual(self.guard.stat().st_mode & 0o777, 0o444)
        self.assertEqual(self.original.stat().st_mode & 0o777, 0o400)
        with self.assertRaisesRegex(ValueError, "ATTRIBUTION_FILE_SCOPE"):
            identity.attribution.read_private(self.guard, 1048576)
        self.write_guard({**self.payload, "original_config_sha256": "0" * 64})
        self.assertFalse(self.inspect()["original_configuration"]["guard_pin_matches"])

    def test_exception_does_not_admit_other_public_or_writable_configs(self):
        other = self.home / "unrelated.json"
        other.write_bytes(self.guard.read_bytes()); other.chmod(0o444)
        with self.assertRaisesRegex(ValueError, "CONFIGURATION_GUARD_FILE_SCOPE"):
            self.inspect(other)
        for changed in ({**self.payload, "schema": "unrelated"},
                        {**self.payload, "trade_authority": True}):
            self.write_guard(changed)
            with self.assertRaisesRegex(ValueError, "CONFIGURATION_GUARD_SCOPE"):
                self.inspect()
        for mode in (0o644, 0o664):
            self.write_guard(self.payload, mode)
            with self.assertRaisesRegex(ValueError, "ATTRIBUTION_FILE_SCOPE"):
                self.inspect()

    def test_foreign_owner_and_links_cannot_use_the_read_only_exception(self):
        with patch.object(identity.os, "geteuid", return_value=identity.os.geteuid() + 1):
            with self.assertRaisesRegex(ValueError, "CONFIGURATION_GUARD_FILE_SCOPE"):
                self.inspect()
        link = self.home / "alias"
        link.hardlink_to(self.guard)
        with self.assertRaisesRegex(ValueError, "CONFIGURATION_GUARD_FILE_SCOPE"):
            self.inspect()
        link.unlink()
        self.guard.unlink()
        self.guard.symlink_to(self.original)
        with self.assertRaises(OSError):self.inspect()

    def test_changed_or_oversize_guard_is_not_a_stable_identity(self):
        real_read = identity.os.read
        def changing_read(fd, count):
            raw = real_read(fd, count)
            self.guard.chmod(0o600)
            with self.guard.open("ab") as stream:stream.write(b"\n")
            self.guard.chmod(0o444)
            return raw
        with patch.object(identity.os, "read", side_effect=changing_read):
            with self.assertRaisesRegex(ValueError, "CONFIGURATION_CHANGED_DURING_INSPECTION"):
                self.inspect()
        self.guard.chmod(0o600)
        self.guard.write_bytes(b" " * (1048576 + 1))
        self.guard.chmod(0o444)
        with self.assertRaisesRegex(ValueError, "CONFIGURATION_GUARD_FILE_SCOPE"):
            self.inspect()


if __name__ == "__main__":
    unittest.main()
