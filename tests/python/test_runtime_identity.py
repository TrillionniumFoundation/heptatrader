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


if __name__ == "__main__":
    unittest.main()
