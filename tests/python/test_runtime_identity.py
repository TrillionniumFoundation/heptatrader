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


if __name__ == "__main__":
    unittest.main()
