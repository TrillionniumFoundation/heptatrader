"""Pinned client selection uses local disposable executables, never a socket."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_native_reader as transport
import heptatrader_attributed_driver as launcher

spec = importlib.util.spec_from_file_location("native_client_driver_test", ROOT / "scripts/heptatrader_paper_driver.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class NativeClientBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hepta-local-client-pins-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bindings = {}
        for name in transport.NATIVE_CLIENT_NAMES:
            path = self.root / name / "python3"
            path.parent.mkdir()
            shutil.copyfile("/usr/bin/python3", path)
            path.chmod(0o500)
            self.bindings[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_declaration_requires_exact_three_distinct_bindings(self):
        transport.validate_client_bindings(self.bindings)
        for bad in ({}, dict(self.bindings, unexpected=self.bindings["heptactl"]),
                    {name: item for name, item in self.bindings.items() if name != "hepta-sessionctl"}):
            with self.subTest(keys=set(bad)), self.assertRaisesRegex(ValueError, "BINDINGS_INCOMPLETE"):
                transport.validate_client_bindings(bad)
        bad = copy.deepcopy(self.bindings)
        bad["hepta-sessionctl"] = dict(bad["heptactl"])
        with self.assertRaisesRegex(ValueError, "BINDINGS_AMBIGUOUS"):
            transport.validate_client_bindings(bad)

    def test_every_native_exec_uses_verified_inode_and_preserves_original_arguments(self):
        with mock.patch.object(driver, "NATIVE_CLIENTS", self.bindings), \
             mock.patch.object(driver, "NATIVE_BINARY_VERIFIER", transport.verified_binary):
            for name, binding in self.bindings.items():
                with self.subTest(client=name):
                    arguments = [binding["path"], "-I", "-S", "-B", "-c",
                        "import sys; print(' '.join(sys.argv[1:]))", "original-command-id", "/original/outbox/command.hsr1"]
                    result = driver.run(arguments)
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.args, arguments)
                    self.assertEqual(result.stdout.strip(), "original-command-id /original/outbox/command.hsr1")
                    self.assertNotIn("/proc/self/fd", json.dumps(result.args))

    def test_path_replacement_after_validation_cannot_replace_the_launched_image(self):
        binding = self.bindings["hepta-paper-command"]

        @contextlib.contextmanager
        def replace_after_validation(item):
            with transport.verified_binary(item) as fd:
                replacement = self.root / "replacement"
                shutil.copyfile("/usr/bin/echo", replacement)
                replacement.chmod(0o500)
                replacement.replace(item["path"])
                yield fd

        with mock.patch.object(driver, "NATIVE_CLIENTS", self.bindings), \
             mock.patch.object(driver, "NATIVE_BINARY_VERIFIER", replace_after_validation):
            result = driver.run([binding["path"], "-I", "-S", "-B", "-c", "print('original-command-id')"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "original-command-id")
        with mock.patch.object(driver, "NATIVE_CLIENTS", self.bindings), \
             mock.patch.object(driver, "NATIVE_BINARY_VERIFIER", transport.verified_binary), \
             mock.patch.object(driver.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "NATIVE_READ_BINARY_CHANGED"):
                driver.run([binding["path"], "original-command-id"])
        execute.assert_not_called()

    def test_attributed_launcher_binds_all_clients_before_journal_or_driver(self):
        manifest = {"instrument": "EUR.USD", "journal_directory": "/unused",
            "runtime_sha256": {"heptatrader_paper_driver.py": "a" * 64, "hepta_native_reader.py": "b" * 64},
            "native_clients": self.bindings}
        for stream in (False, True):
            with self.subTest(stream=stream):
                value = dict(manifest, native_read_stream=stream)
                if stream:
                    value["native_read_binary"] = self.bindings["heptactl"]
                pinned_driver = SimpleNamespace(main=lambda: 7)
                with mock.patch.object(sys, "argv", ["launcher", "--manifest", "/unused"]), \
                     mock.patch.object(launcher.os, "umask"), \
                     mock.patch.object(launcher, "load_manifest", return_value=(value, "c" * 64)), \
                     mock.patch.object(launcher, "load_verified_module", side_effect=[pinned_driver, transport]), \
                     mock.patch.object(launcher, "Journal", side_effect=OSError("fixture journal")), \
                     contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(launcher.main(), 7)
                self.assertEqual(pinned_driver.NATIVE_CLIENTS, self.bindings)
                self.assertIs(pinned_driver.NATIVE_BINARY_VERIFIER, transport.verified_binary)
                if stream:
                    self.assertEqual(pinned_driver.NATIVE_READ_BINARY, self.bindings["heptactl"])

    def test_stream_cannot_use_a_different_client_release(self):
        manifest = {"instrument": "EUR.USD", "journal_directory": "/unused", "native_read_stream": True,
            "runtime_sha256": {"heptatrader_paper_driver.py": "a" * 64, "hepta_native_reader.py": "b" * 64},
            "native_clients": self.bindings,
            "native_read_binary": dict(self.bindings["heptactl"], sha256="0" * 64)}
        with mock.patch.object(sys, "argv", ["launcher", "--manifest", "/unused"]), \
             mock.patch.object(launcher.os, "umask"), \
             mock.patch.object(launcher, "load_manifest", return_value=(manifest, "c" * 64)), \
             mock.patch.object(launcher, "load_verified_module", side_effect=[SimpleNamespace(), transport]), \
             mock.patch.object(launcher, "Journal") as journal:
            with self.assertRaisesRegex(ValueError, "NATIVE_CLIENT_READ_BINDING_MISMATCH"):
                launcher.main()
        journal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
